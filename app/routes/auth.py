# app/routes/auth.py
from fastapi import APIRouter, Depends, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session

from app.database.db import get_db
from app.models.user import User
from app.schemas.auth import OperationStatus, TokenResponse
from app.schemas.user import UserCreate, UserRead, UserUpdate, PasswordChange
from app.services.auth import AuthService, get_current_user_dep

router = APIRouter(prefix="/auth")


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    return AuthService.register(db, user_in)


@router.post("/login", response_model=TokenResponse)
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = AuthService.authenticate(db, form_data.username, form_data.password)
    token = AuthService.create_token(user.id)
    return TokenResponse(access_token=token, token_type="bearer")


@router.get("/me", response_model=UserRead)
def read_me(user: User = Depends(get_current_user_dep)):
    return user


@router.patch("/me", response_model=UserRead)
def update_me(
    payload: UserUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    return AuthService.update_user(db, user, payload)


@router.post("/change-password", response_model=OperationStatus)
def change_password(
    data: PasswordChange,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user_dep),
):
    AuthService.change_password(db, user, data)
    return OperationStatus(ok=True)
