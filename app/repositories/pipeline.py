from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.course_graph import CourseGraph
from app.models.course_generation_settings import CourseGenerationSettings
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
                Document.is_deleted.is_(False),
            )
            .order_by(Document.id, Document.version)
            .all()
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
                GenerationRun.status == "succeeded",
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
