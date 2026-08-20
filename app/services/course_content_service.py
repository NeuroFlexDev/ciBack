from pathlib import Path
from uuid import uuid4
import codecs
import zipfile

from fastapi import HTTPException, UploadFile
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.course_graph import CourseGraph
from app.models.document import Document
from app.models.domain_enums import CourseGraphStatus, DocumentStatus
from app.repositories.course_content import CourseContentRepository
from app.schemas.course_graph import (
    CanvasOut,
    CanvasPut,
    CanvasVersionListOut,
    CanvasVersionOut,
    CanvasVersionSummary,
)
from app.services.file_storage import FileStorage, UploadTooLargeError


ALLOWED_UPLOADS = {
    ".pdf": {"application/pdf"},
    ".docx": {
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    },
    ".txt": {"text/plain"},
}

DOCX_REQUIRED_MEMBERS = {"[Content_Types].xml", "word/document.xml"}


def _course_not_found() -> HTTPException:
    return HTTPException(status_code=404, detail="Курс не найден")


def _invalid_file() -> HTTPException:
    return HTTPException(status_code=400, detail="Файл пустой или повреждён")


def _validate_upload_content(upload: UploadFile, suffix: str) -> None:
    source = upload.file
    try:
        source.seek(0)
        first_byte = source.read(1)
        if not first_byte:
            raise _invalid_file()
        source.seek(0)

        if suffix == ".pdf":
            if source.read(5) != b"%PDF-":
                raise _invalid_file()
        elif suffix == ".docx":
            try:
                with zipfile.ZipFile(source) as archive:
                    if not DOCX_REQUIRED_MEMBERS.issubset(archive.namelist()):
                        raise _invalid_file()
            except (zipfile.BadZipFile, OSError):
                raise _invalid_file()
        elif suffix == ".txt":
            decoder = codecs.getincrementaldecoder("utf-8-sig")("strict")
            total = 0
            while chunk := source.read(1024 * 1024):
                total += len(chunk)
                if total > settings.max_document_bytes:
                    raise UploadTooLargeError
                if b"\x00" in chunk:
                    raise _invalid_file()
                decoder.decode(chunk)
            decoder.decode(b"", final=True)
    except HTTPException:
        raise
    except UploadTooLargeError:
        raise
    except (UnicodeError, ValueError):
        raise _invalid_file()
    finally:
        source.seek(0)


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
    def list_canvas_versions(
        db: Session, course_id: int, owner_id: int, limit: int, offset: int
    ) -> CanvasVersionListOut:
        course = CourseContentRepository.get_owned_course(db, course_id, owner_id)
        if course is None:
            raise _course_not_found()
        graphs, total = CourseContentRepository.list_graph_versions(
            db, course_id=course_id, limit=limit, offset=offset
        )
        return CanvasVersionListOut(
            items=[
                CanvasVersionSummary(
                    graph_id=graph.id,
                    version=graph.version,
                    status=graph.status,
                    is_current=course.current_graph_id == graph.id,
                    created_by=graph.created_by,
                    created_at=graph.created_at,
                    updated_at=graph.updated_at,
                )
                for graph in graphs
            ],
            total=total,
            limit=limit,
            offset=offset,
        )

    @staticmethod
    def get_canvas_version(
        db: Session, course_id: int, owner_id: int, version: int
    ) -> CanvasVersionOut:
        course = CourseContentRepository.get_owned_course(db, course_id, owner_id)
        if course is None:
            raise _course_not_found()
        graph = CourseContentRepository.get_graph_version(
            db, course_id=course_id, version=version
        )
        if graph is None:
            raise HTTPException(status_code=404, detail="Версия canvas не найдена")
        return CanvasVersionOut(
            graph_id=graph.id,
            course_id=course_id,
            version=graph.version,
            status=graph.status,
            is_current=course.current_graph_id == graph.id,
            nodes=graph.nodes,
            edges=graph.edges,
            created_by=graph.created_by,
            created_at=graph.created_at,
            updated_at=graph.updated_at,
        )

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
            requested: dict[str, set[int]] = {}
            for node in payload.nodes:
                requested.setdefault(node.type, set()).add(int(node.id.split(":", 1)[1]))
            expected_ids = {node.id for node in payload.nodes}
            actual_ids = CourseContentRepository.valid_canvas_node_ids(
                db, course_id=course.id, requested=requested
            )
            if actual_ids != expected_ids:
                raise HTTPException(
                    status_code=422,
                    detail="Canvas contains a missing, deleted, or foreign course entity",
                )
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
        replace_document_id: int | None = None,
    ) -> Document:
        course = CourseContentRepository.get_owned_course(db, course_id, owner_id)
        if course is None:
            raise _course_not_found()

        superseded = None
        if replace_document_id is not None:
            superseded = CourseContentRepository.get_current_owned_document(
                db,
                document_id=replace_document_id,
                course_id=course_id,
                owner_id=owner_id,
            )
            if superseded is None:
                raise HTTPException(status_code=404, detail="Заменяемый документ не найден")

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

        try:
            _validate_upload_content(upload, suffix)
        except UploadTooLargeError:
            raise HTTPException(status_code=413, detail="Файл превышает допустимый размер")

        storage_key = f"{owner_id}/{course_id}/{uuid4().hex}{suffix}"
        try:
            stored = storage.save(upload.file, storage_key)
        except UploadTooLargeError:
            raise HTTPException(status_code=413, detail="Файл превышает допустимый размер")
        if stored.size_bytes == 0:
            storage.delete(storage_key)
            raise _invalid_file()

        document = Document(
            storage_key=storage_key,
            owner_id=owner_id,
            course_id=course_id,
            document_key=(superseded.document_key if superseded else uuid4().hex),
            version=(superseded.version + 1 if superseded else 1),
            is_current=superseded is None,
            supersedes_document_id=(superseded.id if superseded else None),
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
