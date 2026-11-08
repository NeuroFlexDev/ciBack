"""Run the production agents on a PDF in an isolated, resumable evaluation DB.

No production database is used. Credentials come from .env or --credentials.
Page numbers are one-based PDF pages, preserved in every source citation.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def _evaluation_arguments():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf", type=Path, required=True)
    parser.add_argument("--pages", help="Inclusive PDF page range, e.g. 18-44; default: whole PDF")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--credentials", type=Path)
    parser.add_argument("--title", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--audience", default="Студенты первого курса ИТ-направлений")
    parser.add_argument("--lessons", type=int, default=6)
    parser.add_argument("--max-calls", type=int, default=60)
    parser.add_argument("--max-tokens", type=int, default=600000)
    parser.add_argument(
        "--max-rubles",
        type=float,
        default=150,
        help="Conservative text-generation reservation cap for this isolated evaluation",
    )
    args = parser.parse_args()
    if not 1 <= args.lessons <= 100:
        parser.error("--lessons must be between 1 and 100")
    if args.max_rubles <= 0:
        parser.error("--max-rubles must be positive")
    return parser, args


class _MaterialEvaluation:
    """One isolated evaluation and its durable budget/report state."""

    def progress(self, stage, percentage):
        event = dict(
            stage=stage, progress=percentage, seconds=round(time.monotonic() - self.started, 2)
        )
        self.report["stages"].append(event)
        if self.run is not None:
            self.run.current_stage = stage
            self.run.progress_percent = percentage
            self.session.commit()
        print(json.dumps(event, ensure_ascii=False), flush=True)

    def tracked_generate(self, *a, **kw):
        from fastapi import HTTPException

        from app.ai.models import model_policy
        from app.services.generation_service import generate_from_prompt, render_prompt

        def stop_before_request(message):
            error = HTTPException(402, message)
            error.request_sent = False
            raise error

        request = kw["request"]
        role = request.agent_role
        # Evaluation-only budget, sequential calls. Prices are an explicit
        # snapshot; unknown models stop rather than silently bypassing the cap.
        from decimal import Decimal

        from app.ai import gateway
        from app.services.generation_service import agent_messages

        prices = {
            "openai/gpt-5.4-mini": (83, 497),
            "anthropic/claude-sonnet-4.6": (332, 1658),
            "google/gemini-2.5-flash": (33, 276),
        }
        chosen = request.model or (
            model_policy(role).fallback if request.fallback_model else model_policy(role).model
        )
        if chosen not in prices or (request.provider or "vsellm") != "vsellm":
            stop_before_request("Evaluation price snapshot missing for selected provider/model")
        rate_in, rate_out = prices[chosen]
        estimated_in = gateway.token_count(
            agent_messages(
                render_prompt(a[0], **{k: v for k, v in kw.items() if k != "request"}),
                expect_json=True,
                repair_feedback=request.repair_feedback,
            )
        )
        reservation = Decimal(estimated_in * rate_in + request.max_tokens * rate_out) / 1_000_000
        consumed = Decimal(str(self.report.get("text_budget_reserved_rub", "0")))
        if consumed + reservation > Decimal(str(self.args.max_rubles)):
            stop_before_request("Isolated evaluation text budget reached")
        self.report["text_budget_reserved_rub"] = str(consumed + reservation)
        dump(self.budget_path, {"reserved_rub": self.report["text_budget_reserved_rub"]})
        self.report["prompt_sizes"].append(
            dict(
                agent=role,
                bytes=len(
                    render_prompt(a[0], **{k: v for k, v in kw.items() if k != "request"}).encode()
                ),
            )
        )
        if request.repair_feedback:
            self.report["repairs"].append(
                dict(agent=role, diagnostic=request.repair_feedback[:3000])
            )
        dump(
            self.out / "progress.json",
            {k: self.report[k] for k in ("stages", "repairs", "prompt_sizes")},
        )
        print(
            json.dumps(
                dict(request=role, repair=bool(request.repair_feedback)), ensure_ascii=False
            ),
            flush=True,
        )
        response = generate_from_prompt(*a, **kw)
        raw_dir = self.out / "raw-responses"
        raw_dir.mkdir(exist_ok=True)
        dump(raw_dir / f"{time.time_ns()}-{role}.json", response)
        return response

    def _extract_sources(self):
        import pymupdf

        from app.core.config import settings
        from app.services.document_processing import chunk_blocks, extract_blocks

        self.progress("extraction", 0)
        blocks_path = self.out / "blocks.json"
        if blocks_path.exists():
            from app.services.document_processing import ExtractedBlock

            blocks = [ExtractedBlock(**b) for b in json.loads(blocks_path.read_text())]
        else:
            # OCR only the requested pages, preserving original page numbers.
            # Extracting the whole textbook can require OCR on unrelated covers.
            with (
                pymupdf.open(stream=self.content, filetype="pdf") as pdf,
                pymupdf.open() as selected_pdf,
            ):
                selected_pdf.insert_pdf(pdf, from_page=self.first - 1, to_page=self.last - 1)
                blocks = [
                    replace(
                        block, page=block.page + self.first - 1 if block.page is not None else None
                    )
                    for block in extract_blocks(selected_pdf.tobytes(), "application/pdf")
                ]
            dump(blocks_path, [asdict(b) for b in blocks])
        selected = [b for b in blocks if self.first <= b.page <= self.last]
        self.chunks_data = chunk_blocks(
            selected,
            max_chars=settings.DOCUMENT_CHUNK_CHARS,
            overlap_chars=settings.DOCUMENT_CHUNK_OVERLAP_CHARS,
        )
        full_size = self.full_text_size
        self.selected_size = sum(len(c["text"]) for c in self.chunks_data)
        self.report["extraction"] = dict(
            pdf_pages=self.page_count,
            full_characters=full_size,
            full_book_over_limit=full_size > settings.AI_MAX_SOURCE_CHARS,
            selected_pages=[self.first, self.last],
            selected_characters=self.selected_size,
            chunks=len(self.chunks_data),
            source_character_limit=settings.AI_MAX_SOURCE_CHARS,
        )
        if not self.chunks_data:
            raise ValueError("No text was extracted in the selected page range")
        if self.selected_size > settings.AI_MAX_SOURCE_CHARS:
            raise ValueError(
                "Selected source corpus exceeds AI_MAX_SOURCE_CHARS; choose an explicit chapter range"
            )

    def _prepare_evaluation_data(self):
        from app.ai.retrieval import PersistentVectorStore, persist_embeddings
        from app.core.config import settings
        from app.models.course import Course
        from app.models.document import Document, DocumentChunk
        from app.models.generation_run import GenerationRun
        from app.models.user import User
        from app.services.retrieval_service import RetrievalQuery, RetrievalService
        from app.services.source_catalog_service import build_source_catalog

        self.user = self.session.query(User).first()
        if self.user is None:
            self.user = User(email="materials-evaluation@example.invalid", password_hash="unused")
            self.session.add(self.user)
            self.session.flush()
            self.course = Course(owner_id=self.user.id, name=self.args.title, status="ready")
            self.session.add(self.course)
            self.session.flush()
            self.run = GenerationRun(
                owner_id=self.user.id,
                course_id=self.course.id,
                run_type="graph_generation",
                status="running",
            )
            self.session.add(self.run)
            doc = Document(
                owner_id=self.user.id,
                course_id=self.course.id,
                storage_key=str(self.args.pdf.resolve()),
                version=1,
                status="indexed",
                content_hash=self.file_hash,
                source_type="upload",
                original_filename=self.args.pdf.name,
                mime_type="application/pdf",
                size_bytes=len(self.content),
            )
            self.session.add(doc)
            self.session.flush()
            self.session.add_all(
                [
                    DocumentChunk(document_id=doc.id, document_version=1, **c)
                    for c in self.chunks_data
                ]
            )
            self.session.commit()
        self.course = self.session.query(Course).one()
        self.run = self.session.query(GenerationRun).one()
        self.run.status = "running"
        self.run.settings_snapshot = self.snapshot
        self.run.finished_at = None
        if self.course.current_graph is None:
            self.course.status = "generating"
        self.session.commit()
        docs = self.session.query(Document).all()
        chunks = self.session.query(DocumentChunk).order_by(DocumentChunk.chunk_index).all()
        self.sources = build_source_catalog(docs, max_chars=settings.AI_MAX_SOURCE_CHARS)
        assert len(self.sources) == len(chunks)
        assert sum(len(s["quote"]) for s in self.sources) == self.selected_size
        dump(self.out / "sources.json", self.sources)
        self.progress("embeddings", 2)
        for chunk, embedding_id in zip(
            chunks, persist_embeddings(self.session, chunks), strict=True
        ):
            chunk.embedding_id = embedding_id
        self.session.commit()
        self.report["retrieval"] = []
        for query in (
            "Что такое подмножество?",
            "Как найти дополнение множества?",
            "Как доказать тождество методом двух включений?",
        ):
            found = RetrievalService.search_course(
                self.session,
                course_id=self.course.id,
                owner_id=self.user.id,
                vector_store=PersistentVectorStore(self.session),
                search_query=RetrievalQuery(query=query, limit=3),
            )
            self.report["retrieval"].append(found.model_dump(mode="json"))

    def _publish_evaluation(self):
        from app.models.course_graph import CourseGraph
        from app.models.course_source_link import CourseSourceLink
        from app.services.course_materialization_service import CourseMaterializationService
        from app.services.course_publication_service import CoursePublicationService
        from app.services.source_catalog_service import graph_source_links

        if self.course.current_graph is None:
            graph = CourseGraph(
                course_id=self.course.id,
                version=1,
                nodes=[],
                edges=[],
                created_by=self.user.id,
                status="draft",
            )
            self.session.add(graph)
            self.session.flush()
            self.course.current_graph = graph
            materialized = CourseMaterializationService.materialize(
                self.session, course=self.course, nodes=self.result.nodes, edges=self.result.edges
            )
            graph.nodes = materialized.pop("canvas_nodes")
            graph.edges = materialized.pop("canvas_edges")
            self.report["materialized"] = materialized
            self.report["learning_map"] = CourseMaterializationService.materialize_learning_map(
                self.session, course=self.course, result=self.result.result
            )
            links = graph_source_links(graph.nodes, self.sources)
            self.session.add_all(
                [
                    CourseSourceLink(
                        course_id=self.course.id, graph_id=graph.id, run_id=self.run.id, **link
                    )
                    for link in links
                ]
            )
            self.report["source_links"] = len(links)
            self.run.status = "completed"
            self.course.status = "ready"
            self.session.commit()
            self.report["publication"] = CoursePublicationService.publish(
                self.session, self.course.id, self.user.id
            )

    def _finalize_report(self):
        from app.models.agent_artifact import AgentArtifact
        from app.models.ai_state import AICall

        self.session.rollback()
        self.report["seconds"] = round(time.monotonic() - self.started, 2)
        if self.run is not None:
            self.run.status = "completed" if self.report["success"] else "failed"
            self.run.error_code = None if self.report["success"] else "evaluation_failed"
            self.run.error_message = (
                None
                if self.report["success"]
                else str(self.report.get("error", self.report.get("error_type")))[:2000]
            )
            self.run.finished_at = datetime.utcnow()
            self.run.latency_ms = int(self.report["seconds"] * 1000)
            if not self.report["success"] and self.course.current_graph is None:
                self.course.status = "generation_failed"
            self.session.commit()
        self.report["calls"] = [
            dict(
                id=c.id,
                agent=c.agent,
                model=c.model,
                status=c.status,
                input_tokens=c.input_tokens,
                output_tokens=c.output_tokens,
                cached_tokens=c.cached_tokens,
                latency_ms=c.latency_ms,
                error_code=c.error_code,
                routing=c.routing,
            )
            for c in self.session.query(AICall).order_by(AICall.id).all()
        ]
        dump(
            self.out / "agent-artifacts.json",
            [
                dict(
                    agent=a.agent,
                    sequence=a.sequence,
                    status=a.status,
                    payload=a.payload,
                    error=a.error,
                )
                for a in self.session.query(AgentArtifact).all()
            ],
        )
        path = self.out / "report.json"
        if path.exists():
            path.rename(self.out / f"report-{time.time_ns()}.json")
        dump(path, self.report)
        # A rejected course can still be inspected without bypassing publication.
        if not self.report["success"] and (self.out / "checkpoints.sqlite").exists():
            from app.ai.checkpoints import checkpoint_store
            from app.schemas.agentic_pipeline import AgenticPipelineResult

            try:
                with checkpoint_store() as saver:
                    checkpoint = next(saver.list(None, limit=1), None)
                values = checkpoint.checkpoint["channel_values"] if checkpoint else {}
                candidate = AgenticPipelineResult.model_validate(
                    {
                        k: values[k]
                        for k in (
                            "ingestion",
                            "competency_map",
                            "course_plan",
                            "writer",
                            "assessment",
                            "qa",
                        )
                    }
                )
                dump(
                    self.out / "course-candidate.json",
                    {
                        **self.report,
                        "candidate_only": True,
                        "artifacts": candidate.model_dump(mode="json"),
                        "checkpoint_revision": values.get("revision"),
                    },
                )
            except (KeyError, ValueError):
                pass  # An earlier failure may not yet have a structurally complete draft.
        print(
            json.dumps(
                {
                    k: v
                    for k, v in self.report.items()
                    if k in {"success", "error", "error_type", "seconds", "qa", "checkpoint_replay"}
                },
                ensure_ascii=False,
                default=str,
            ),
            flush=True,
        )
        self.session.close()

    def _prepare_report(self):
        import pymupdf

        from app.core.config import settings
        from app.database.db import Base, SessionLocal, engine

        self.content = self.args.pdf.read_bytes()
        self.file_hash = hashlib.sha256(self.content).hexdigest()
        with pymupdf.open(stream=self.content, filetype="pdf") as pdf:
            self.page_count = len(pdf)
            self.full_text_size = sum(len(page.get_text()) for page in pdf)
        try:
            self.first, self.last = (
                map(int, self.args.pages.split("-")) if self.args.pages else (1, self.page_count)
            )
            if not 1 <= self.first <= self.last <= self.page_count:
                raise ValueError
        except ValueError:
            self.parser.error("--pages must be an inclusive range within the PDF")
        self.snapshot = dict(
            goal=self.args.goal,
            target_audience=self.args.audience,
            difficulty="basic",
            language="ru",
            lesson_count=self.args.lessons,
            module_tests_enabled=True,
            final_test_enabled=True,
        )
        self.identity = dict(
            sha256=self.file_hash,
            pdf_pages=[self.first, self.last],
            title=self.args.title,
            settings=self.snapshot,
        )
        manifest = self.out / "manifest.json"
        if manifest.exists() and json.loads(manifest.read_text()) != self.identity:
            self.parser.error(
                "Output directory belongs to different inputs; choose another directory"
            )
        dump(manifest, self.identity)
        Base.metadata.create_all(engine)
        self.session = SessionLocal()
        self.report = dict(
            success=False, scope=self.identity, stages=[], repairs=[], prompt_sizes=[]
        )
        self.budget_path = self.out / "text-budget.json"
        previous_report = self.out / "report.json"
        # Reservations survive process restarts; reusing an output directory must
        # not silently grant another paid budget. This evaluator is sequential.
        previous_budget = (
            json.loads(self.budget_path.read_text()).get("reserved_rub", "0")
            if self.budget_path.exists()
            else json.loads(previous_report.read_text()).get("text_budget_reserved_rub", "0")
            if previous_report.exists()
            else "0"
        )
        self.report["text_budget_reserved_rub"] = previous_budget
        from app.ai.models import model_policy

        self.report["model_policy"] = {
            role: asdict(model_policy(role))
            for role in (
                "ingestion",
                "competency_mapper",
                "course_architect",
                "lesson_writer",
                "assessment",
                "critic_qa",
            )
        }
        self.report["limits"] = dict(
            calls=self.args.max_calls,
            budget_tokens=self.args.max_tokens,
            context_budget=settings.AI_MAX_INPUT_TOKENS,
            revisions=settings.AI_MAX_REVISIONS,
        )

    def execute(self):
        self.parser, self.args = _evaluation_arguments()
        self.root = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(self.root))
        self.out = self.args.output_dir.resolve()
        self.out.mkdir(parents=True, exist_ok=True)
        os.environ.update(
            DATABASE_URL="sqlite:///" + str(self.out / "evaluation.sqlite"),
            DEBUG="false",
            ENV="evaluation",
            JWT_SECRET=secrets.token_urlsafe(48),
            AI_CHECKPOINT_SQLITE_PATH=str(self.out / "checkpoints.sqlite"),
            AI_MAX_CALLS_PER_RUN=str(self.args.max_calls),
            AI_MAX_TOKENS_PER_RUN=str(self.args.max_tokens),
        )
        from dotenv import dotenv_values

        key = (
            self.args.credentials.read_text().strip()
            if self.args.credentials
            else dotenv_values(self.root / ".env").get("VSELLM_API_KEY")
        )
        if key:
            os.environ["VSELLM_API_KEY"] = key
        if (self.out / "tessdata").is_dir():
            os.environ.setdefault("TESSDATA_PREFIX", str(self.out / "tessdata"))

        import app.models  # noqa: F401
        from app.core.config import settings
        from app.models.ai_state import AICall
        from app.services.agent_runtime import AgentRuntime
        from app.services.agentic_course_pipeline import AgenticCoursePipeline

        self._prepare_report()
        self.started = time.monotonic()
        self.run = None

        try:
            if not settings.VSELLM_API_KEY.get_secret_value():
                raise ValueError("VSELLM credentials are not configured")
            self._extract_sources()
            self._prepare_evaluation_data()
            self.pipeline = AgenticCoursePipeline(
                runtime=AgentRuntime(
                    db=self.session,
                    run_id=self.run.id,
                    course_id=self.course.id,
                    generate=self.tracked_generate,
                ),
                checkpoint=self.progress,
            )
            self.result = self.pipeline.run(
                course_title=self.course.name,
                settings_snapshot=self.snapshot,
                source_catalog=self.sources,
            )
            self.report.update(
                qa=self.result.qa_summary,
                artifacts=self.result.result.model_dump(mode="json"),
                nodes=self.result.nodes,
                edges=self.result.edges,
            )
            self._publish_evaluation()
            before = self.session.query(AICall).count()
            cache_start = time.monotonic()
            resumed = self.pipeline.run(
                course_title=self.course.name,
                settings_snapshot=self.snapshot,
                source_catalog=self.sources,
            )
            self.report["checkpoint_replay"] = dict(
                additional_llm_calls=self.session.query(AICall).count() - before,
                seconds=round(time.monotonic() - cache_start, 3),
                same_artifacts=resumed.result == self.result.result,
            )
            self.report["success"] = True
        except Exception as exc:
            from fastapi import HTTPException
            from pydantic import ValidationError

            self.report["error_type"] = type(exc).__name__
            if isinstance(exc, ValidationError):
                self.report["error"] = exc.errors(include_input=False, include_url=False)
            elif isinstance(exc, (ValueError, HTTPException)):
                self.report["error"] = str(exc)[:3000]
            else:
                self.report["error"] = "Provider/internal details redacted"
        finally:
            self._finalize_report()
        return 0 if self.report["success"] else 1


def main():
    return _MaterialEvaluation().execute()


if __name__ == "__main__":
    raise SystemExit(main())
