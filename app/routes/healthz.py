import os
import tempfile

from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text

from app.database.db import get_db
from app.services.embedding_service import get_index_status
from sqlalchemy.orm import Session

router = APIRouter()


def _check_temp_storage() -> dict[str, str]:
    path = tempfile.gettempdir()
    writable = os.access(path, os.W_OK)
    return {"status": "up" if writable else "down", "path": path}


def _build_runtime_status(db: Session) -> tuple[bool, dict]:
    db_status = "up"
    try:
        db.execute(text("SELECT 1"))
    except Exception:
        db_status = "down"

    vector_status = get_index_status()
    storage_status = _check_temp_storage()
    queue_status = {"status": "not_configured"}

    ok = db_status == "up" and storage_status["status"] == "up"
    body = {
        "ok": ok,
        "db": db_status,
        "vector_index": vector_status,
        "file_storage": storage_status,
        "queue": queue_status,
    }
    return ok, body


@router.get("/healthz")
def healthz(db: Session = Depends(get_db)):
    ok, body = _build_runtime_status(db)
    if ok:
        return body
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body)


@router.get("/readiness")
def readiness(db: Session = Depends(get_db)):
    ok, body = _build_runtime_status(db)
    if ok:
        return body
    return JSONResponse(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content=body)
