from __future__ import annotations

from typing import TYPE_CHECKING

from sqlalchemy.orm import Session
from app.models.user import User

if TYPE_CHECKING:
    from app.schemas.user import UserUpdate

class UserRepository:
    @staticmethod
    def get_by_email(db: Session, email: str) -> User | None:
        return db.query(User).filter(User.email == email).first()

    @staticmethod
    def get_by_id(db: Session, user_id: int) -> User | None:
        return db.get(User, user_id)

    @staticmethod
    def create(db: Session, email: str, password_hash: str) -> User:
        user = User(email=email, password_hash=password_hash)
        db.add(user)
        db.flush()
        db.refresh(user)
        return user

    @staticmethod
    def update(db: Session, user: User, payload: UserUpdate) -> User:
        if payload.email:
            user.email = payload.email
        db.commit()
        db.refresh(user)
        return user
