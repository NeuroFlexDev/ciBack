import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field, StrictBool, field_validator
from sqlalchemy.orm import Session

from app.core.config import settings
from app.database.db import get_db
from app.models.legal import LegalReceipt, LegalRequest
from app.models.user import User
from app.services.auth_service import get_current_user
from app.services.legal_service import CATALOG, require_current_version, record_receipt

router = APIRouter(prefix="/legal")


class ContactPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    purpose: Literal["enquiry", "support", "rights"]
    name: str = Field(default="", max_length=160)
    email: str = Field(min_length=5, max_length=255)
    message: str = Field(min_length=10, max_length=5000)
    consent: StrictBool = False
    legal_version: str = Field(min_length=1, max_length=32)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Укажите корректный email")
        return value

    @field_validator("message")
    @classmethod
    def message_not_blank(cls, value):
        if len(value.strip()) < 10:
            raise ValueError("Опишите вопрос подробнее")
        return value.strip()


class MarketingChoice(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accepted: StrictBool
    legal_version: str = Field(min_length=1, max_length=32)


@router.get("/catalog")
def catalog():
    return {**CATALOG, "registration_enabled": settings.REGISTRATION_ENABLED,
            "contact_requests_enabled": settings.CONTACT_REQUESTS_ENABLED}


@router.post("/requests", status_code=201)
def contact(payload: ContactPayload, db: Session = Depends(get_db)):
    if not settings.CONTACT_REQUESTS_ENABLED:
        raise HTTPException(503, "Форма пока недоступна. Направьте обращение по почтовому адресу из раздела контактов.")
    # An outdated optional consent must never block exercise of statutory rights.
    if payload.purpose == "enquiry":
        require_current_version(payload.legal_version)
        if not payload.consent:
            raise HTTPException(422, "Для заявки на демонстрацию требуется отдельное согласие на обработку данных обращения.")
    reference = str(uuid.uuid4())
    db.add(LegalRequest(reference=reference, purpose=payload.purpose,
                        email=payload.email, name=payload.name.strip(), message=payload.message))
    if payload.purpose == "enquiry":
        record_receipt(db, email=payload.email, slug="contact-consent", context="request:" + reference)
    db.flush()
    return {"reference": reference}


@router.get("/me/receipts")
def receipts(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    rows = db.query(LegalReceipt).filter_by(user_id=user.id).order_by(LegalReceipt.id.desc()).all()
    marketing = next((row for row in rows if row.document_slug == "marketing-consent"), None)
    valid = False
    if marketing and marketing.action == "accepted":
        created = marketing.created_at.replace(tzinfo=timezone.utc) if marketing.created_at.tzinfo is None else marketing.created_at
        valid = created + timedelta(days=365) > datetime.now(timezone.utc)
    return {"marketing_active": valid, "receipts": [
        {"id": row.id, "slug": row.document_slug, "version": row.document_version,
         "sha256": row.document_sha256, "document": row.document_snapshot,
         "action": row.action, "created_at": row.created_at} for row in rows
    ]}


@router.put("/me/marketing")
def marketing(payload: MarketingChoice, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    if payload.accepted:
        require_current_version(payload.legal_version)
    # Withdrawal is allowed even when the client has an older document version.
    record_receipt(db, email=user.email, user_id=user.id, slug="marketing-consent",
                   context="account", action="accepted" if payload.accepted else "withdrawn")
    return {"marketing_active": payload.accepted}
