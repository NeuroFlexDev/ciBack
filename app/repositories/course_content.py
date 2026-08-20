from sqlalchemy.orm import Session

from app.models.course import Course
from app.models.course_graph import CourseGraph
from app.models.document import Document
from app.models.lesson import Lesson
from app.models.module import Module
from app.models.task import Task
from app.models.test import Test
from sqlalchemy import and_, or_


class CourseContentRepository:
    @staticmethod
    def valid_canvas_node_ids(
        db: Session, *, course_id: int, requested: dict[str, set[int]]
    ) -> set[str]:
        result: set[str] = set()
        if requested.get("module"):
            ids = db.query(Module.id).filter(
                Module.id.in_(requested["module"]), Module.course_id == course_id,
                Module.is_deleted.is_(False),
            )
            result.update(f"module:{item.id}" for item in ids)
        if requested.get("lesson"):
            ids = db.query(Lesson.id).join(Module).filter(
                Lesson.id.in_(requested["lesson"]), Module.course_id == course_id,
                Lesson.is_deleted.is_(False), Module.is_deleted.is_(False),
            )
            result.update(f"lesson:{item.id}" for item in ids)
        if requested.get("test"):
            ids = db.query(Test.id).outerjoin(Module, Test.module_id == Module.id).filter(
                Test.id.in_(requested["test"]), Test.is_deleted.is_(False),
                or_(
                    and_(Module.course_id == course_id, Module.is_deleted.is_(False)),
                    Test.course_id == course_id,
                ),
            )
            result.update(f"test:{item.id}" for item in ids)
        if requested.get("task"):
            ids = db.query(Task.id).join(Module).filter(
                Task.id.in_(requested["task"]), Module.course_id == course_id,
                Task.is_deleted.is_(False), Module.is_deleted.is_(False),
            )
            result.update(f"task:{item.id}" for item in ids)
        return result

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
    def add_graph(db: Session, graph: CourseGraph) -> None:
        db.add(graph)

    @staticmethod
    def list_graph_versions(
        db: Session, *, course_id: int, limit: int, offset: int
    ) -> tuple[list[CourseGraph], int]:
        query = db.query(CourseGraph).filter(
            CourseGraph.course_id == course_id,
            CourseGraph.is_deleted.is_(False),
        )
        total = query.count()
        items = (
            query.order_by(CourseGraph.version.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return items, total

    @staticmethod
    def get_graph_version(
        db: Session, *, course_id: int, version: int
    ) -> CourseGraph | None:
        return (
            db.query(CourseGraph)
            .filter(
                CourseGraph.course_id == course_id,
                CourseGraph.version == version,
                CourseGraph.is_deleted.is_(False),
            )
            .first()
        )

    @staticmethod
    def add_document(db: Session, document: Document) -> None:
        db.add(document)

    @staticmethod
    def get_current_owned_document(
        db: Session, *, document_id: int, course_id: int, owner_id: int
    ) -> Document | None:
        return (
            db.query(Document)
            .filter(
                Document.id == document_id,
                Document.course_id == course_id,
                Document.owner_id == owner_id,
                Document.is_current.is_(True),
                Document.is_deleted.is_(False),
            )
            .first()
        )

    @staticmethod
    def list_documents(
        db: Session,
        *,
        course_id: int,
        owner_id: int,
        limit: int,
        offset: int,
        statuses: tuple[str, ...] | None,
        source_type: str | None,
        sort_by: str,
        sort_order: str,
    ) -> tuple[list[Document], int]:
        query = db.query(Document).filter(
            Document.course_id == course_id,
            Document.owner_id == owner_id,
            Document.is_current.is_(True),
            Document.is_deleted.is_(False),
            Document.status != "archived",
        )
        if statuses is not None:
            query = query.filter(Document.status.in_(statuses))
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
