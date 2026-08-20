from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.agent_artifact import AgentArtifact
from app.models.course import Course
from app.models.course_graph import CourseGraph
from app.models.course_generation_settings import CourseGenerationSettings
from app.models.course_source_link import CourseSourceLink
from app.models.course_update_proposal import CourseUpdateProposal
from app.models.document import Document, DocumentChunk
from app.models.generation_run import GenerationRun


class PipelineRepository:
    @staticmethod
    def get_generation_settings(
        db: Session, course_id: int
    ) -> CourseGenerationSettings | None:
        return (
            db.query(CourseGenerationSettings)
            .filter(
                CourseGenerationSettings.course_id == course_id,
                CourseGenerationSettings.is_deleted.is_(False),
            )
            .first()
        )

    @staticmethod
    def get_owned_document(
        db: Session, document_id: int, owner_id: int
    ) -> Document | None:
        return (
            db.query(Document)
            .join(Course, Course.id == Document.course_id)
            .filter(
                Document.id == document_id,
                Document.owner_id == owner_id,
                Document.is_deleted.is_(False),
                Course.is_deleted.is_(False),
            )
            .first()
        )

    @staticmethod
    def get_owned_course(
        db: Session, course_id: int, owner_id: int, *, for_update: bool = False
    ) -> Course | None:
        query = db.query(Course).filter(
            Course.id == course_id,
            Course.owner_id == owner_id,
            Course.is_deleted.is_(False),
        )
        if for_update:
            query = query.with_for_update()
        return query.first()

    @staticmethod
    def get_owned_run(
        db: Session, run_id: int, owner_id: int
    ) -> GenerationRun | None:
        return (
            db.query(GenerationRun)
            .filter(
                GenerationRun.id == run_id,
                GenerationRun.owner_id == owner_id,
                GenerationRun.is_deleted.is_(False),
            )
            .first()
        )

    @staticmethod
    def indexed_documents(
        db: Session, course_id: int, owner_id: int
    ) -> list[Document]:
        return (
            db.query(Document)
            .filter(
                Document.course_id == course_id,
                Document.owner_id == owner_id,
                Document.status == "indexed",
                Document.is_current.is_(True),
                Document.is_deleted.is_(False),
            )
            .order_by(Document.id, Document.version)
            .all()
        )

    @staticmethod
    def selected_indexed_documents(
        db: Session, course_id: int, owner_id: int, document_ids: list[int]
    ) -> list[Document]:
        return (
            db.query(Document)
            .filter(
                Document.id.in_(document_ids),
                Document.course_id == course_id,
                Document.owner_id == owner_id,
                Document.status == "indexed",
                Document.is_current.is_(True),
                Document.is_deleted.is_(False),
            )
            .order_by(Document.id)
            .all()
        )

    @staticmethod
    def documents_for_snapshot(
        db: Session, course_id: int, owner_id: int, snapshot: list[dict]
    ) -> list[Document]:
        if not snapshot:
            return []
        ids = [item["document_id"] for item in snapshot]
        candidates = (
            db.query(Document)
            .filter(
                Document.id.in_(ids),
                Document.course_id == course_id,
                Document.owner_id == owner_id,
                Document.status == "indexed",
                Document.is_deleted.is_(False),
            )
            .all()
        )
        expected = {
            (item["document_id"], item["version"], item["content_hash"])
            for item in snapshot
        }
        return [
            item
            for item in candidates
            if (item.id, item.version, item.content_hash) in expected
        ]

    @staticmethod
    def active_graph_run(
        db: Session, course_id: int, owner_id: int
    ) -> GenerationRun | None:
        return (
            db.query(GenerationRun)
            .filter(
                GenerationRun.course_id == course_id,
                GenerationRun.owner_id == owner_id,
                GenerationRun.run_type == "graph_generation",
                GenerationRun.status.in_(("queued", "running")),
                GenerationRun.is_deleted.is_(False),
            )
            .order_by(GenerationRun.id.desc())
            .first()
        )

    @staticmethod
    def replace_chunks(
        db: Session, document: Document, chunks: list[dict]
    ) -> list[DocumentChunk]:
        (
            db.query(DocumentChunk)
            .filter(
                DocumentChunk.document_id == document.id,
                DocumentChunk.document_version == document.version,
            )
            .delete(synchronize_session=False)
        )
        models = [
            DocumentChunk(
                document_id=document.id,
                document_version=document.version,
                text=chunk["text"],
                page=chunk.get("page"),
                section=chunk.get("section"),
                metadata_json={
                    **chunk.get("metadata_json", {}),
                    "document_id": document.id,
                    "document_version": document.version,
                    "page": chunk.get("page"),
                    "section": chunk.get("section"),
                    "source": document.original_filename,
                    "source_type": document.source_type,
                    "owner_id": document.owner_id,
                    "organization_id": None,
                    "course_id": document.course_id,
                },
                chunk_index=chunk["chunk_index"],
            )
            for chunk in chunks
        ]
        db.add_all(models)
        db.flush()
        return models

    @staticmethod
    def latest_successful_graph_run(
        db: Session, owner_id: int, course_id: int, fingerprint: str
    ) -> GenerationRun | None:
        return (
            db.query(GenerationRun)
            .filter(
                GenerationRun.owner_id == owner_id,
                GenerationRun.course_id == course_id,
                GenerationRun.run_type == "graph_generation",
                GenerationRun.status.in_(("succeeded", "completed")),
                GenerationRun.input_fingerprint == fingerprint,
                GenerationRun.is_deleted.is_(False),
            )
            .order_by(GenerationRun.id.desc())
            .first()
        )

    @staticmethod
    def graph_by_id(db: Session, graph_id: int) -> CourseGraph | None:
        return (
            db.query(CourseGraph)
            .filter(CourseGraph.id == graph_id, CourseGraph.is_deleted.is_(False))
            .first()
        )

    @staticmethod
    def next_graph_version(db: Session, course_id: int) -> int:
        current = (
            db.query(func.max(CourseGraph.version))
            .filter(CourseGraph.course_id == course_id)
            .scalar()
        )
        return (current or 0) + 1

    @staticmethod
    def get_agent_artifact(
        db: Session, *, run_id: int, agent: str, sequence: int
    ) -> AgentArtifact | None:
        return (
            db.query(AgentArtifact)
            .filter(
                AgentArtifact.run_id == run_id,
                AgentArtifact.agent == agent,
                AgentArtifact.sequence == sequence,
                AgentArtifact.is_deleted.is_(False),
            )
            .first()
        )

    @staticmethod
    def add_agent_artifact(db: Session, artifact: AgentArtifact) -> AgentArtifact:
        db.add(artifact)
        db.flush()
        return artifact

    @staticmethod
    def list_agent_artifacts(
        db: Session, *, run_id: int, owner_id: int
    ) -> list[AgentArtifact] | None:
        if PipelineRepository.get_owned_run(db, run_id, owner_id) is None:
            return None
        return (
            db.query(AgentArtifact)
            .filter(
                AgentArtifact.run_id == run_id,
                AgentArtifact.is_deleted.is_(False),
            )
            .order_by(AgentArtifact.created_at, AgentArtifact.id)
            .all()
        )

    @staticmethod
    def source_links_for_document(
        db: Session,
        *,
        course_id: int,
        graph_id: int,
        document_ids: list[int],
    ) -> list[CourseSourceLink]:
        if not document_ids:
            return []
        return (
            db.query(CourseSourceLink)
            .filter(
                CourseSourceLink.course_id == course_id,
                CourseSourceLink.graph_id == graph_id,
                CourseSourceLink.document_id.in_(document_ids),
                CourseSourceLink.is_deleted.is_(False),
            )
            .order_by(CourseSourceLink.node_id, CourseSourceLink.ref_id)
            .all()
        )

    @staticmethod
    def add_source_links(
        db: Session, links: list[CourseSourceLink]
    ) -> None:
        db.add_all(links)

    @staticmethod
    def add_update_proposal(
        db: Session, proposal: CourseUpdateProposal
    ) -> CourseUpdateProposal:
        db.add(proposal)
        db.flush()
        return proposal

    @staticmethod
    def list_update_proposals(
        db: Session,
        *,
        course_id: int,
        owner_id: int,
        limit: int,
        offset: int,
    ) -> tuple[list[CourseUpdateProposal], int]:
        if PipelineRepository.get_owned_course(db, course_id, owner_id) is None:
            return [], -1
        query = db.query(CourseUpdateProposal).filter(
            CourseUpdateProposal.course_id == course_id,
            CourseUpdateProposal.is_deleted.is_(False),
        )
        return (
            query.order_by(CourseUpdateProposal.id.desc())
            .offset(offset)
            .limit(limit)
            .all(),
            query.count(),
        )
