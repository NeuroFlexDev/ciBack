import hashlib
import json
from pathlib import Path

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.legal import LegalReceipt


CATALOG = json.loads((Path(__file__).resolve().parents[1] / "legal/catalog.json").read_text(encoding="utf-8"))
DOCUMENTS = {doc["slug"]: doc for doc in CATALOG["documents"]}
for _document in DOCUMENTS.values():
    _unsigned = {key: value for key, value in _document.items() if key != "sha256"}
    _encoded = json.dumps(_unsigned, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    if hashlib.sha256(_encoded.encode()).hexdigest() != _document["sha256"]:
        raise RuntimeError("Legal document checksum mismatch")


def require_current_version(version: str):
    if version != CATALOG["version"]:
        raise HTTPException(409, "Документы обновились. Обновите страницу и ознакомьтесь с новой редакцией.")


def record_receipt(db: Session, *, email: str, slug: str, context: str, user_id: int | None = None, action: str = "accepted"):
    document = DOCUMENTS[slug]
    receipt = LegalReceipt(
        user_id=user_id, subject_email=email, document_slug=slug,
        document_version=document["version"], document_sha256=document["sha256"],
        document_snapshot=document, action=action, context=context,
    )
    db.add(receipt)
    db.flush()
    return receipt
