from io import BytesIO
from zipfile import ZipFile

from app.core.config import Settings
from app.models.document import Document
from app.services.file_storage import LocalFileStorage, get_file_storage
from main import app
from tests.factories import make_course


DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


def _docx_bytes() -> bytes:
    output = BytesIO()
    with ZipFile(output, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", "<document/>")
    return output.getvalue()


def test_supported_document_signatures(
    client, db_session, auth_user, auth_headers, tmp_path
):
    app.dependency_overrides[get_file_storage] = lambda: LocalFileStorage(
        tmp_path, 25 * 1024 * 1024
    )
    course = make_course(db_session, owner_id=auth_user.id)
    cases = [
        ("source.pdf", b"%PDF-1.7\nbody", "application/pdf"),
        ("source.docx", _docx_bytes(), DOCX_MIME),
        ("source.txt", "Текст".encode(), "text/plain"),
    ]

    for filename, content, content_type in cases:
        response = client.post(
            f"/api/courses/{course.id}/documents",
            files={"file": (filename, content, content_type)},
            headers=auth_headers,
        )
        assert response.status_code == 202
        assert response.json()["status"] == "processing"
        assert response.json()["content_type"] == content_type


def test_document_content_validation(
    client, db_session, auth_user, auth_headers, tmp_path
):
    app.dependency_overrides[get_file_storage] = lambda: LocalFileStorage(
        tmp_path, 25 * 1024 * 1024
    )
    course = make_course(db_session, owner_id=auth_user.id)
    invalid_cases = [
        ("broken.pdf", b"not-pdf", "application/pdf", 400),
        ("broken.docx", b"PK-not-a-zip", DOCX_MIME, 400),
        ("binary.txt", b"text\x00binary", "text/plain", 400),
        ("invalid.txt", b"\xff\xfe", "text/plain", 400),
        ("wrong.pdf", b"%PDF-1.7", "text/plain", 415),
    ]

    for filename, content, content_type, expected in invalid_cases:
        response = client.post(
            f"/api/courses/{course.id}/documents",
            files={"file": (filename, content, content_type)},
            headers=auth_headers,
        )
        assert response.status_code == expected

    assert client.post(
        f"/api/courses/{course.id}/documents", headers=auth_headers
    ).status_code == 400


def test_public_status_mapping_filter_and_detail(
    client, db_session, auth_user, auth_headers
):
    course = make_course(db_session, owner_id=auth_user.id)
    internal_statuses = ["uploaded", "queued", "processing", "indexed", "failed"]
    for index, internal_status in enumerate(internal_statuses):
        db_session.add(
            Document(
                storage_key=f"{index}.txt",
                owner_id=auth_user.id,
                course_id=course.id,
                version=1,
                status=internal_status,
                content_hash=str(index),
                source_type="upload",
                original_filename=f"{index}.txt",
                mime_type="text/plain",
                size_bytes=1,
                processing_error="safe error" if internal_status == "failed" else None,
            )
        )
    db_session.commit()

    response = client.get(
        f"/api/courses/{course.id}/documents",
        params={"status": "processing"},
        headers=auth_headers,
    )
    assert response.status_code == 200
    assert response.json()["total"] == 3
    assert {item["status"] for item in response.json()["items"]} == {"processing"}

    ready = client.get(
        f"/api/courses/{course.id}/documents",
        params={"status": "ready"},
        headers=auth_headers,
    ).json()
    assert ready["total"] == 1
    detail = client.get(
        f"/api/documents/{ready['items'][0]['id']}", headers=auth_headers
    )
    assert detail.json()["status"] == "ready"
    assert set(detail.json()) == {
        "id", "course_id", "original_filename", "content_type", "size_bytes",
        "source_type", "version", "status", "error_message", "created_at",
        "updated_at",
    }

    failed = client.get(
        f"/api/courses/{course.id}/documents",
        params={"status": "error"},
        headers=auth_headers,
    ).json()
    assert failed["items"][0]["error_message"] == "safe error"


def test_document_size_settings_support_new_and_legacy_names():
    common = {
        "DATABASE_URL": "sqlite:///:memory:",
        "JWT_SECRET": "test-only-jwt-secret-at-least-32-bytes",
        "_env_file": None,
    }
    configured = Settings(**common, MAX_DOCUMENT_SIZE_MB=25)
    legacy = Settings(**common, MAX_DOCUMENT_SIZE_MB=25, MAX_UPLOAD_BYTES=1024)

    assert configured.max_document_bytes == 25 * 1024 * 1024
    assert legacy.max_document_bytes == 1024


def test_openapi_exposes_final_document_contract(client):
    schema = client.get("/openapi.json").json()
    upload = schema["paths"]["/api/courses/{course_id}/documents"]["post"]
    assert "202" in upload["responses"]
    assert "201" not in upload["responses"]
    schemas = schema["components"]["schemas"]
    assert "DocumentUploadResponse" in schemas
    assert "DocumentListResponse" in schemas
    assert set(schemas["DocumentStatus"]["enum"]) == {"processing", "ready", "error"}
