# app/repositories/user.py
from sqlalchemy.orm import Session
from app.models.user import User
from app.schemas.user import UserCreate, UserUpdate

class UserRepository:
    @staticmethod
    def get_by_email(db: Session, email: str) -> User | None:
        return db.query(User).filter(User.email == email, User.is_deleted == False).first()

    @staticmethod
    def get_by_id(db: Session, user_id: int) -> User | None:
        return db.query(User).get(user_id)

    @staticmethod
    def create(db: Session, user_in: UserCreate, hashed_password: str) -> User:
        user = User(
            email=user_in.email,
            full_name=user_in.full_name,
            hashed_password=hashed_password
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return user

    @staticmethod
    def update(db: Session, user: User, payload: UserUpdate) -> User:
        if payload.email:
            user.email = payload.email
        if payload.full_name:
            user.full_name = payload.full_name
        db.commit()
        db.refresh(user)
        return user
