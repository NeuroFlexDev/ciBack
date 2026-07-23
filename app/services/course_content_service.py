from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.course_graph import CourseGraph
from app.models.document import Document
from app.models.domain_enums import CourseGraphStatus, DocumentStatus
from app.repositories.course_content import CourseContentRepository
from app.schemas.course_graph import CanvasOut, CanvasPut
from app.services.file_storage import FileStorage, UploadTooLargeError


ALLOWED_UPLOADS = {
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    },
    ".txt": {"text/plain"},
}


def _course_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Курс не найден")


def _canvas_out(course_id: int, graph: CourseGraph | None) -> CanvasOut:
    if graph is None or graph.is_deleted:
        return CanvasOut(
            course_id=course_id,
            graph_id=None,
            version=0,
            nodes=[],
            edges=[],
            status=None,
            created_by=None,
            created_at=None,
            updated_at=None,
        )
    return CanvasOut(
        course_id=course_id,
        graph_id=graph.id,
        version=graph.version,
        nodes=graph.nodes,
        edges=graph.edges,
        status=graph.status,
        created_by=graph.created_by,
        created_at=graph.created_at,
        updated_at=graph.updated_at,
    )


class CourseContentService:
    @staticmethod
    def get_canvas(db: Session, course_id: int, owner_id: int) -> CanvasOut:
        course = CourseContentRepository.get_owned_course(db, course_id, owner_id)
        if course is None:
            raise _course_not_found()
        return _canvas_out(course.id, course.current_graph)

    @staticmethod
    def save_canvas(
        db: Session, course_id: int, owner_id: int, payload: CanvasPut
    ) -> CanvasOut:
        try:
            course = CourseContentRepository.get_owned_course(
                db, course_id, owner_id, for_update=True
            )
            if course is None:
                raise _course_not_found()
            current = course.current_graph
            current_version = (
                current.version if current is not None and not current.is_deleted else 0
            )
            if payload.version != current_version:
                raise HTTPException(
                    status_code=409,
                    detail={
                        "message": "Canvas version conflict",
                        "expected_version": payload.version,
                        "current_version": current_version,
                    },
                )

            graph = CourseGraph(
                course_id=course.id,
                version=current_version + 1,
                nodes=[node.model_dump() for node in payload.nodes],
                edges=[edge.model_dump() for edge in payload.edges],
                created_by=owner_id,
                status=CourseGraphStatus.PUBLISHED.value,
            )
            if current is not None:
                current.status = CourseGraphStatus.ARCHIVED.value
            CourseContentRepository.add_graph(db, graph)
            db.flush()
            course.current_graph = graph
            db.commit()
            db.refresh(graph)
            return _canvas_out(course.id, graph)
        except HTTPException:
            raise
        except IntegrityError:
            db.rollback()
            raise HTTPException(status_code=409, detail="Canvas version conflict")
        except Exception:
            db.rollback()
            raise

    @staticmethod
    def upload_document(
        db: Session,
        *,
        course_id: int,
        owner_id: int,
        upload: UploadFile,
        storage: FileStorage,
    ) -> Document:
        course = CourseContentRepository.get_owned_course(db, course_id, owner_id)
        if course is None:
            raise _course_not_found()

        original_name = "".join(
            character
            for character in Path(upload.filename or "").name
            if character.isprintable() and character not in "\r\n\t"
        ).strip()[:512]
        if not original_name:
            raise HTTPException(status_code=400, detail="Некорректное имя файла")
        suffix = Path(original_name).suffix.lower()
        if suffix not in ALLOWED_UPLOADS or upload.content_type not in ALLOWED_UPLOADS[suffix]:
            raise HTTPException(status_code=415, detail="Неподдерживаемый тип файла")

        storage_key = f"{owner_id}/{course_id}/{uuid4().hex}{suffix}"
        try:
            stored = storage.save(upload.file, storage_key)
        except UploadTooLargeError:
            raise HTTPException(status_code=413, detail="Файл превышает допустимый размер")
        if stored.size_bytes == 0:
            storage.delete(storage_key)
            raise HTTPException(status_code=400, detail="Пустой файл")

        document = Document(
            storage_key=storage_key,
            owner_id=owner_id,
            course_id=course_id,
            version=1,
            status=DocumentStatus.UPLOADED.value,
            content_hash=stored.content_hash,
            source_type="upload",
            original_filename=original_name,
            mime_type=upload.content_type,
            size_bytes=stored.size_bytes,
        )
        try:
            CourseContentRepository.add_document(db, document)
            db.commit()
            db.refresh(document)
            return document
        except Exception:
            db.rollback()
            storage.delete(storage_key)
            raise
