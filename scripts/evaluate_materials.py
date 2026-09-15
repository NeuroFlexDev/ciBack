"""Run the production agents on a PDF in an isolated, resumable evaluation DB.

No production database is used. Credentials come from .env or --credentials.
Page numbers are one-based PDF pages, preserved in every source citation.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import sys
import time


def dump(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, default=str))


def main():
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
    args = parser.parse_args()
    if not 1 <= args.lessons <= 100:
        parser.error("--lessons must be between 1 and 100")
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root))
    out = args.output_dir.resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.environ.update(
        DATABASE_URL="sqlite:///" + str(out / "evaluation.sqlite"), DEBUG="false", ENV="evaluation",
        JWT_SECRET="isolated-material-evaluation-secret-at-least-32-bytes",
        AI_CHECKPOINT_SQLITE_PATH=str(out / "checkpoints.sqlite"),
        AI_MAX_CALLS_PER_RUN=str(args.max_calls), AI_MAX_TOKENS_PER_RUN=str(args.max_tokens),
    )
    from dotenv import dotenv_values
    key = args.credentials.read_text().strip() if args.credentials else dotenv_values(root / ".env").get("VSELLM_API_KEY")
    if key:
        os.environ["VSELLM_API_KEY"] = key
    if (out / "tessdata").is_dir():
        os.environ.setdefault("TESSDATA_PREFIX", str(out / "tessdata"))

    import pymupdf
    import app.models  # noqa: F401
    from app.core.config import settings
    from app.database.db import Base, engine, SessionLocal
    from app.models.user import User
    from app.models.course import Course
    from app.models.document import Document, DocumentChunk
    from app.models.generation_run import GenerationRun
    from app.models.agent_artifact import AgentArtifact
    from app.models.ai_state import AICall
    from app.services.document_processing import extract_blocks, chunk_blocks
    from app.services.source_catalog_service import build_source_catalog, graph_source_links
    from app.ai.retrieval import persist_embeddings, PersistentVectorStore
    from app.services.retrieval_service import RetrievalService
    from app.services.agent_runtime import AgentRuntime
    from app.services.generation_service import generate_from_prompt, render_prompt
    from app.services.agentic_course_pipeline import AgenticCoursePipeline

    content = args.pdf.read_bytes()
    file_hash = hashlib.sha256(content).hexdigest()
    with pymupdf.open(stream=content, filetype="pdf") as pdf:
        page_count = len(pdf)
    try:
        first, last = map(int, args.pages.split("-")) if args.pages else (1, page_count)
        if not 1 <= first <= last <= page_count:
            raise ValueError
    except ValueError:
        parser.error("--pages must be an inclusive range within the PDF")
    snapshot = dict(goal=args.goal, target_audience=args.audience, difficulty="basic", language="ru",
                    lesson_count=args.lessons, module_tests_enabled=True, final_test_enabled=True)
    identity = dict(sha256=file_hash, pdf_pages=[first, last], title=args.title, settings=snapshot)
    manifest = out / "manifest.json"
    if manifest.exists() and json.loads(manifest.read_text()) != identity:
        parser.error("Output directory belongs to different inputs; choose another directory")
    dump(manifest, identity)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    report = dict(success=False, scope=identity, stages=[], repairs=[], prompt_sizes=[])
    from app.ai.models import model_policy
    report["model_policy"] = {role: asdict(model_policy(role)) for role in (
        "ingestion", "competency_mapper", "course_architect", "lesson_writer", "assessment", "critic_qa")}
    report["limits"] = dict(calls=args.max_calls, budget_tokens=args.max_tokens,
                           context_budget=settings.AI_MAX_INPUT_TOKENS, revisions=settings.AI_MAX_REVISIONS)
    started = time.monotonic()
    run = None

    def progress(stage, percentage):
        event = dict(stage=stage, progress=percentage, seconds=round(time.monotonic() - started, 2))
        report["stages"].append(event)
        if run is not None:
            run.current_stage = stage
            run.progress_percent = percentage
            session.commit()
        print(json.dumps(event, ensure_ascii=False), flush=True)

    def tracked_generate(*a, **kw):
        role = kw.get("agent_role")
        report["prompt_sizes"].append(dict(agent=role, bytes=len(render_prompt(a[0], **kw).encode())))
        if kw.get("repair_feedback"):
            report["repairs"].append(dict(agent=role, diagnostic=kw["repair_feedback"][:3000]))
        dump(out / "progress.json", {k: report[k] for k in ("stages", "repairs", "prompt_sizes")})
        print(json.dumps(dict(request=role, repair=bool(kw.get("repair_feedback"))), ensure_ascii=False), flush=True)
        response = generate_from_prompt(*a, **kw)
        raw_dir = out / "raw-responses"
        raw_dir.mkdir(exist_ok=True)
        dump(raw_dir / f"{time.time_ns()}-{role}.json", response)
        return response

    try:
        if not settings.VSELLM_API_KEY.get_secret_value():
            raise ValueError("VSELLM credentials are not configured")
        progress("extraction", 0)
        blocks_path = out / "blocks.json"
        if blocks_path.exists():
            from app.services.document_processing import ExtractedBlock
            blocks = [ExtractedBlock(**b) for b in json.loads(blocks_path.read_text())]
        else:
            blocks = extract_blocks(content, "application/pdf")
            dump(blocks_path, [asdict(b) for b in blocks])
        selected = [b for b in blocks if first <= b.page <= last]
        chunks_data = chunk_blocks(selected, max_chars=settings.DOCUMENT_CHUNK_CHARS,
                                   overlap_chars=settings.DOCUMENT_CHUNK_OVERLAP_CHARS)
        full_size = sum(len(b.text) for b in blocks)
        selected_size = sum(len(c["text"]) for c in chunks_data)
        report["extraction"] = dict(pdf_pages=page_count, full_characters=full_size,
            full_book_over_limit=full_size > settings.AI_MAX_SOURCE_CHARS,
            selected_pages=[first, last], selected_characters=selected_size, chunks=len(chunks_data),
            source_character_limit=settings.AI_MAX_SOURCE_CHARS)
        if not chunks_data:
            raise ValueError("No text was extracted in the selected page range")
        if selected_size > settings.AI_MAX_SOURCE_CHARS:
            raise ValueError("Selected source corpus exceeds AI_MAX_SOURCE_CHARS; choose an explicit chapter range")
        user = session.query(User).first()
        if user is None:
            user = User(email="materials-evaluation@example.invalid", password_hash="unused")
            session.add(user); session.flush()
            course = Course(owner_id=user.id, name=args.title, status="ready")
            session.add(course); session.flush()
            run = GenerationRun(owner_id=user.id, course_id=course.id, run_type="graph_generation", status="running")
            session.add(run)
            doc = Document(owner_id=user.id, course_id=course.id, storage_key=str(args.pdf.resolve()),
                version=1, status="indexed", content_hash=file_hash, source_type="upload",
                original_filename=args.pdf.name, mime_type="application/pdf", size_bytes=len(content))
            session.add(doc); session.flush()
            session.add_all([DocumentChunk(document_id=doc.id, document_version=1, **c) for c in chunks_data])
            session.commit()
        course = session.query(Course).one()
        run = session.query(GenerationRun).one()
        run.status = "running"
        run.settings_snapshot = snapshot
        run.finished_at = None
        if course.current_graph is None:
            course.status = "generating"
        session.commit()
        docs = session.query(Document).all()
        chunks = session.query(DocumentChunk).order_by(DocumentChunk.chunk_index).all()
        sources = build_source_catalog(docs, max_chars=settings.AI_MAX_SOURCE_CHARS)
        assert len(sources) == len(chunks)
        assert sum(len(s["quote"]) for s in sources) == selected_size
        dump(out / "sources.json", sources)
        progress("embeddings", 2)
        for chunk, embedding_id in zip(chunks, persist_embeddings(session, chunks)):
            chunk.embedding_id = embedding_id
        session.commit()
        report["retrieval"] = []
        for query in ("Что такое подмножество?", "Как найти дополнение множества?", "Как доказать тождество методом двух включений?"):
            found = RetrievalService.search_course(session, course_id=course.id, owner_id=user.id,
                query=query, limit=3, vector_store=PersistentVectorStore(session))
            report["retrieval"].append(found.model_dump(mode="json"))
        pipeline = AgenticCoursePipeline(runtime=AgentRuntime(db=session, run_id=run.id,
            course_id=course.id, generate=tracked_generate), checkpoint=progress)
        result = pipeline.run(course_title=course.name, settings_snapshot=snapshot, source_catalog=sources)
        report.update(qa=result.qa_summary, artifacts=result.result.model_dump(mode="json"),
                      nodes=result.nodes, edges=result.edges)
        from app.services.course_materialization_service import CourseMaterializationService
        from app.services.course_publication_service import CoursePublicationService
        from app.models.course_graph import CourseGraph
        from app.models.course_source_link import CourseSourceLink
        if course.current_graph is None:
            graph = CourseGraph(course_id=course.id, version=1, nodes=[], edges=[], created_by=user.id, status="draft")
            session.add(graph); session.flush(); course.current_graph = graph
            materialized = CourseMaterializationService.materialize(session, course=course, nodes=result.nodes, edges=result.edges)
            graph.nodes = materialized.pop("canvas_nodes"); graph.edges = materialized.pop("canvas_edges")
            report["materialized"] = materialized
            report["learning_map"] = CourseMaterializationService.materialize_learning_map(session, course=course, result=result.result)
            links = graph_source_links(graph.nodes, sources)
            session.add_all([CourseSourceLink(course_id=course.id, graph_id=graph.id, run_id=run.id, **link) for link in links])
            report["source_links"] = len(links)
            run.status = "completed"; course.status = "ready"; session.commit()
            report["publication"] = CoursePublicationService.publish(session, course.id, user.id)
        before = session.query(AICall).count()
        cache_start = time.monotonic()
        resumed = pipeline.run(course_title=course.name, settings_snapshot=snapshot, source_catalog=sources)
        report["checkpoint_replay"] = dict(additional_llm_calls=session.query(AICall).count() - before,
            seconds=round(time.monotonic() - cache_start, 3), same_artifacts=resumed.result == result.result)
        report["success"] = True
    except Exception as exc:
        from fastapi import HTTPException
        from pydantic import ValidationError
        report["error_type"] = type(exc).__name__
        if isinstance(exc, ValidationError):
            report["error"] = exc.errors(include_input=False, include_url=False)
        elif isinstance(exc, (ValueError, HTTPException)):
            report["error"] = str(exc)[:3000]
        else:
            report["error"] = "Provider/internal details redacted"
    finally:
        session.rollback()
        report["seconds"] = round(time.monotonic() - started, 2)
        if run is not None:
            run.status = "completed" if report["success"] else "failed"
            run.error_code = None if report["success"] else "evaluation_failed"
            run.error_message = None if report["success"] else str(report.get("error", report.get("error_type")))[:2000]
            run.finished_at = datetime.utcnow()
            run.latency_ms = int(report["seconds"] * 1000)
            if not report["success"] and course.current_graph is None:
                course.status = "generation_failed"
            session.commit()
        report["calls"] = [dict(agent=c.agent, model=c.model, status=c.status, input_tokens=c.input_tokens,
            output_tokens=c.output_tokens, cached_tokens=c.cached_tokens, latency_ms=c.latency_ms, error_code=c.error_code)
            for c in session.query(AICall).order_by(AICall.id).all()]
        dump(out / "agent-artifacts.json", [dict(agent=a.agent, sequence=a.sequence, status=a.status,
            payload=a.payload, error=a.error) for a in session.query(AgentArtifact).all()])
        path = out / "report.json"
        if path.exists():
            path.rename(out / f"report-{time.time_ns()}.json")
        dump(path, report)
        # A rejected course can still be inspected without bypassing publication.
        if not report["success"] and (out / "checkpoints.sqlite").exists():
            from app.ai.checkpoints import checkpoint_store
            from app.schemas.agentic_pipeline import AgenticPipelineResult
            try:
                with checkpoint_store() as saver:
                    checkpoint = next(saver.list(None, limit=1), None)
                values = checkpoint.checkpoint["channel_values"] if checkpoint else {}
                candidate = AgenticPipelineResult.model_validate({k: values[k] for k in (
                    "ingestion", "competency_map", "course_plan", "writer", "assessment", "qa")})
                dump(out / "course-candidate.json", {**report, "candidate_only": True,
                    "artifacts": candidate.model_dump(mode="json"), "checkpoint_revision": values.get("revision")})
            except (KeyError, ValueError):
                pass  # An earlier failure may not yet have a structurally complete draft.
        print(json.dumps({k: v for k, v in report.items() if k in {"success", "error", "error_type", "seconds", "qa", "checkpoint_replay"}}, ensure_ascii=False, default=str), flush=True)
        session.close()
    return 0 if report["success"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
