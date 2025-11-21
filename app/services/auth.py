# app/services/auth.py
from app.repositories.user import UserRepository
from app.schemas.user import UserCreate, PasswordChange
from app.services.security import (
    hash_password, verify_password, create_access_token, ALGORITHM, SECRET_KEY
)
from sqlalchemy.orm import Session
from fastapi import HTTPException, status
from app.models.user import User
from jose import JWTError, jwt
from fastapi import Depends
from app.database.db import get_db
from fastapi.security import OAuth2PasswordBearer

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

class AuthService:
    @staticmethod
    def register(db: Session, user_in: UserCreate) -> User:
        if UserRepository.get_by_email(db, user_in.email):
            raise HTTPException(400, "Email already registered")
        hashed_pw = hash_password(user_in.password)
        return UserRepository.create(db, user_in, hashed_pw)

    @staticmethod
    def authenticate(db: Session, email: str, password: str) -> User:
        user = UserRepository.get_by_email(db, email)
        if not user or not verify_password(password, user.hashed_password):
            raise HTTPException(status_code=400, detail="Incorrect email or password")
        return user

    @staticmethod
    def create_token(user_id: int) -> str:
        return create_access_token({"sub": str(user_id)})

    @staticmethod
    def get_current_user(db: Session, token: str) -> User:
        credentials_exception = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
        try:
            payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
            user_id: int = payload.get("sub")
            if user_id is None:
                raise credentials_exception
        except JWTError:
            raise credentials_exception
        user = UserRepository.get_by_id(db, user_id)
        if user is None:
            raise credentials_exception
        return user

    @staticmethod
    def change_password(db: Session, user: User, password_change: PasswordChange):
        if not verify_password(password_change.old_password, user.hashed_password):
            raise HTTPException(400, "Wrong old password")
        user.hashed_password = hash_password(password_change.new_password)
        db.commit()
        return user

def get_current_user_dep(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)):
    return AuthService.get_current_user(db, token)