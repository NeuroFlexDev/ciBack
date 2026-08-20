from __future__ import annotations

import json

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.course_update_proposal import CourseUpdateProposal
from app.models.document import Document
from app.models.generation_run import GenerationRun
from app.repositories.pipeline import PipelineRepository
from app.schemas.agentic_pipeline import SourceRef, UpdateArtifact
from app.services.agent_runtime import AgentRuntime
from app.services.generation_service import generate_from_prompt
from app.services.source_catalog_service import build_source_catalog


def _json(value) -> str:
    if hasattr(value, "model_dump"):
        value = value.model_dump(mode="json")
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        default=lambda item: (
            item.model_dump(mode="json") if hasattr(item, "model_dump") else str(item)
        ),
    )


class CourseUpdateService:
    """Analyze document lineage changes without mutating generated content."""

    @staticmethod
    def analyze_replacement(
        db: Session,
        *,
        document: Document,
        run: GenerationRun,
        generate=generate_from_prompt,
    ) -> CourseUpdateProposal | None:
        if document.supersedes_document_id is None:
            return None
        previous = db.get(Document, document.supersedes_document_id)
        course = PipelineRepository.get_owned_course(
            db, document.course_id, document.owner_id
        )
        if previous is None or course is None or course.current_graph is None:
            return None
        if previous.content_hash == document.content_hash:
            return None

        links = PipelineRepository.source_links_for_document(
            db,
            course_id=course.id,
            graph_id=course.current_graph.id,
            document_ids=[previous.id],
        )
        if not links:
            return None

        previous_sources: dict[str, SourceRef] = {}
        for link in links:
            try:
                durable_chunk_id = int(link.ref_id.rsplit(":", 1)[-1])
            except (TypeError, ValueError):
                durable_chunk_id = link.chunk_id
            if durable_chunk_id is None:
                raise ValueError("Source link has no durable chunk locator")
            previous_sources.setdefault(
                link.ref_id,
                SourceRef(
                    id=link.ref_id,
                    document_id=previous.id,
                    document_version=link.document_version,
                    document_content_hash=previous.content_hash,
                    chunk_id=durable_chunk_id,
                    chunk_index=link.chunk_index,
                    page=link.page,
                    section=link.section,
                    quote=link.excerpt,
                ),
            )
        current_catalog = build_source_catalog(
            [document], max_chars=60_000
        )
        if not current_catalog:
            return None

        affected_node_ids = sorted({item.node_id for item in links})
        graph_nodes = {
            str(item["id"]): item for item in course.current_graph.nodes
        }
        logical_graph_nodes: dict[str, list[str]] = {}
        for persisted_id, node in graph_nodes.items():
            logical_id = node.get("logical_id")
            if isinstance(logical_id, str):
                logical_graph_nodes.setdefault(logical_id, []).append(persisted_id)
        affected_nodes = [
            graph_nodes[item]
            for item in affected_node_ids
            if item in graph_nodes
        ]
        pipeline_artifacts = []
        artifact_logical_ids: set[str] = set()
        cited_artifact_ids: set[str] = set()
        source_ids = set(previous_sources)

        def collect(value) -> None:
            if isinstance(value, dict):
                logical_id = value.get("id")
                if isinstance(logical_id, str) and ":" in logical_id:
                    artifact_logical_ids.add(logical_id)
                    if source_ids.intersection(value.get("source_ref_ids") or []):
                        cited_artifact_ids.add(logical_id)
                for child in value.values():
                    collect(child)
            elif isinstance(value, list):
                for child in value:
                    collect(child)

        source_run_id = next((item.run_id for item in links if item.run_id), None)
        if source_run_id is not None:
            stored_artifacts = PipelineRepository.list_agent_artifacts(
                db, run_id=source_run_id, owner_id=document.owner_id
            ) or []
            pipeline_artifacts = [
                {
                    "agent": item.agent,
                    "artifact": item.artifact,
                    "sequence": item.sequence,
                    "payload": item.payload,
                }
                for item in stored_artifacts
                if item.status == "completed"
            ]
            collect(pipeline_artifacts)
        affected_artifact_ids = sorted(
            set(affected_node_ids) | cited_artifact_ids
        )
        runtime = AgentRuntime(
            db=db,
            run_id=run.id,
            course_id=course.id,
            generate=generate,
        )
        artifact = runtime.execute(
            agent="update",
            artifact="update_proposal",
            sequence=0,
            template_name="update_agent_prompt.j2",
            response_model=UpdateArtifact,
            prompt_context={
                "previous_source_refs_json": _json(list(previous_sources.values())),
                "current_source_refs_json": _json(current_catalog),
                "previous_source_catalog_json": _json(list(previous_sources.values())),
                "current_source_catalog_json": _json(current_catalog),
                "document_changes_json": _json(
                    [
                        {
                            "document_id": document.id,
                            "change_type": "modified",
                            "before_version": previous.version,
                            "before_content_hash": previous.content_hash,
                            "after_version": document.version,
                            "after_content_hash": document.content_hash,
                        }
                    ]
                ),
                "affected_nodes_json": _json(affected_nodes),
                "current_pipeline_artifacts_json": _json(
                    {
                        "affected_artifact_ids": affected_artifact_ids,
                        "agent_artifacts": pipeline_artifacts,
                        "affected_nodes": affected_nodes,
                        "course_graph": {
                            "nodes": course.current_graph.nodes,
                            "edges": course.current_graph.edges,
                        },
                    }
                ),
                "course_graph_json": _json(
                    {
                        "nodes": course.current_graph.nodes,
                        "edges": course.current_graph.edges,
                    }
                ),
            },
            max_tokens=4096,
        )
        known_node_ids = set(graph_nodes) | set(logical_graph_nodes) | artifact_logical_ids
        invented = {
            item.affected_artifact_id
            for item in artifact.impacts
            if item.affected_artifact_id not in known_node_ids
        }
        if invented:
            raise ValueError(
                f"Update Agent referenced unknown course nodes: {sorted(invented)}"
            )
        proposed_ids: set[str] = set()
        for item in artifact.impacts:
            if item.proposed_action == "no_change":
                continue
            target = item.affected_artifact_id
            matches = logical_graph_nodes.get(target, [])
            if len(matches) > 1:
                raise ValueError(f"Ambiguous logical course node reference: {target}")
            proposed_ids.add(matches[0] if matches else target)
        proposed_ids = sorted(proposed_ids)
        proposal = CourseUpdateProposal(
            course_id=course.id,
            document_id=document.id,
            base_graph_id=course.current_graph.id,
            detected_by_run_id=run.id,
            source_versions=[
                {"document_id": previous.id, "version": previous.version},
                {"document_id": document.id, "version": document.version},
            ],
            source_hashes=[previous.content_hash, document.content_hash],
            affected_node_ids=proposed_ids,
            proposed_diff={
                "operations": [
                    item.model_dump(mode="json") for item in artifact.proposed_diff
                ],
                "impacts": [item.model_dump(mode="json") for item in artifact.impacts],
                "requires_human_review": artifact.requires_human_review,
            },
            summary=artifact.summary,
            status="proposed",
        )
        PipelineRepository.add_update_proposal(db, proposal)
        db.commit()
        db.refresh(proposal)
        return proposal

    @staticmethod
    def list_proposals(
        db: Session,
        *,
        course_id: int,
        owner_id: int,
        limit: int,
        offset: int,
    ) -> tuple[list[CourseUpdateProposal], int]:
        if PipelineRepository.get_owned_course(db, course_id, owner_id) is None:
            raise HTTPException(status_code=404, detail="Курс не найден")
        return PipelineRepository.list_update_proposals(
            db,
            course_id=course_id,
            owner_id=owner_id,
            limit=limit,
            offset=offset,
        )
