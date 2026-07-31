from sqlalchemy.orm import Session, joinedload

from app.models.course import Course
from app.models.document import Document, DocumentChunk
from app.models.domain_enums import DocumentStatus


class RetrievalRepository:
    @staticmethod
    def get_owned_course(db: Session, course_id: int, owner_id: int) -> Course | None:
        return (
            db.query(Course)
            .filter(Course.id == course_id, Course.owner_id == owner_id)
            .first()
        )

    @staticmethod
    def accessible_chunks(
        db: Session, course_id: int, owner_id: int
    ) -> list[DocumentChunk]:
        return (
            db.query(DocumentChunk)
            .join(Document, DocumentChunk.document_id == Document.id)
            .options(joinedload(DocumentChunk.document))
            .filter(
                Document.course_id == course_id,
                Document.owner_id == owner_id,
                Document.status == DocumentStatus.INDEXED.value,
                Document.is_deleted.is_(False),
                DocumentChunk.is_deleted.is_(False),
                DocumentChunk.document_version == Document.version,
            )
            .order_by(
                DocumentChunk.document_id,
                DocumentChunk.document_version,
                DocumentChunk.chunk_index,
            )
            .all()
        )
