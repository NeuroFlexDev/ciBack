from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import func
from sqlalchemy.orm import Session
from app.database.db import get_db
from app.models.notifications import UserNotification
from app.models.integrations import MobilePushPreference
from app.services.auth_service import get_current_user

router = APIRouter(prefix="/notifications")


def inbox(db, user):
    return db.query(UserNotification).filter_by(user_id=user.id, organization_id=user.organization_id)


class Preferences(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)
    push_enabled: bool = True
    assignment_enabled: bool = True
    deadline_enabled: bool = True
    result_enabled: bool = True
    review_enabled: bool = True
    generation_enabled: bool = True
    timezone: str = Field(default="UTC", max_length=64)
    quiet_enabled: bool = False
    quiet_start: str = Field(default="22:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")
    quiet_end: str = Field(default="09:00", pattern=r"^(?:[01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def valid_schedule(self):
        try:
            ZoneInfo(self.timezone)
        except (ValueError, ZoneInfoNotFoundError):
            raise ValueError("Укажите часовой пояс IANA")
        if self.quiet_enabled and self.quiet_start == self.quiet_end:
            raise ValueError("Начало и конец тихих часов должны различаться")
        return self


class ReadAll(BaseModel):
    model_config = ConfigDict(extra="forbid")
    through_id: int = Field(ge=0)


def serialize(row):
    return {key: getattr(row, key) for key in ("id", "category", "title", "body", "resource_type", "resource_id", "created_at", "read_at")}


@router.get("")
def list_notifications(before_id: int | None = Query(None, ge=1), limit: int = Query(30, ge=1, le=100),
                       unread: bool = False, category: Literal["assignment", "deadline", "result", "review", "generation"] | None = None,
                       db: Session = Depends(get_db), user=Depends(get_current_user)):
    base = inbox(db, user)
    unread_count = base.filter(UserNotification.read_at.is_(None)).count()
    latest_id = base.with_entities(func.max(UserNotification.id)).scalar() or 0
    query = base
    if before_id:
        query = query.filter(UserNotification.id < before_id)
    if unread:
        query = query.filter(UserNotification.read_at.is_(None))
    if category:
        query = query.filter_by(category=category)
    rows = query.order_by(UserNotification.id.desc()).limit(limit + 1).all()
    return {"items": [serialize(r) for r in rows[:limit]], "unread_count": unread_count,
            "latest_id": latest_id, "next_cursor": rows[limit - 1].id if len(rows) > limit else None}


@router.post("/read-all")
def mark_all(payload: ReadAll, db: Session = Depends(get_db), user=Depends(get_current_user)):
    count = inbox(db, user).filter(UserNotification.id <= payload.through_id, UserNotification.read_at.is_(None)).update({"read_at": datetime.utcnow()}, synchronize_session=False)
    db.commit()
    return {"updated": count}


@router.get("/preferences", response_model=Preferences)
def get_preferences(db: Session = Depends(get_db), user=Depends(get_current_user)):
    row = db.get(MobilePushPreference, user.id)
    return Preferences.model_validate(row) if row else Preferences()


@router.put("/preferences", response_model=Preferences)
def put_preferences(payload: Preferences, db: Session = Depends(get_db), user=Depends(get_current_user)):
    # Lock the principal to serialize concurrent first-time preference creation.
    from app.models.user import User
    db.query(User).filter_by(id=user.id).with_for_update().one()
    row = db.get(MobilePushPreference, user.id)
    if row is None:
        row = MobilePushPreference(user_id=user.id)
        db.add(row)
    for key, value in payload.model_dump().items():
        setattr(row, key, value)
    db.commit()
    return Preferences.model_validate(row)


@router.get("/{notification_id}")
def get_notification(notification_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    row = inbox(db, user).filter_by(id=notification_id).first()
    if row is None:
        raise HTTPException(404, "Уведомление недоступно")
    return serialize(row)


@router.post("/{notification_id}/read")
def mark_read(notification_id: int, db: Session = Depends(get_db), user=Depends(get_current_user)):
    row = inbox(db, user).filter_by(id=notification_id).with_for_update().first()
    if row is None:
        raise HTTPException(404, "Уведомление недоступно")
    row.read_at = row.read_at or datetime.utcnow()
    db.commit()
    return serialize(row)
