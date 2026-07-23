from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.course_graph import CourseGraph
from app.models.document import Document


class CourseContentRepository:
    @staticmethod
    def get_owned_course(
        db: Session, course_id: int, owner_id: int, *, for_update: bool = False
    ) -> Course | None:
        query = db.query(Course).filter(
            Course.id == course_id, Course.owner_id == owner_id
        )
        if for_update:
            query = query.with_for_update()
        return query.first()

    @staticmethod
    def add_graph(db: Session, graph: CourseGraph) -> None:
        db.add(graph)

    @staticmethod
    def add_document(db: Session, document: Document) -> None:
        db.add(document)

    @staticmethod
    def list_documents(
        db: Session,
        *,
        course_id: int,
        owner_id: int,
        limit: int,
        offset: int,
        status: str | None,
        source_type: str | None,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[Document], int]:
        query = db.query(Document).filter(
            Document.course_id == course_id,
            Document.owner_id == owner_id,
            Document.is_deleted.is_(False),
        )
        if status is not None:
            query = query.filter(Document.status == status)
        if source_type is not None:
            query = query.filter(Document.source_type == source_type)

        total = query.count()
        sort_columns = {
            "created_at": Document.created_at,
            "original_filename": Document.original_filename,
            "size_bytes": Document.size_bytes,
            "status": Document.status,
        }
        column = sort_columns[sort_by]
        order = column.asc() if sort_order == "asc" else column.desc()
        items = query.order_by(order, Document.id.desc()).offset(offset).limit(limit).all()
        return items, total
