# app/routes/auth.py
from fastapi import APIRouter, Depends
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from app.schemas.user import UserCreate, UserRead, UserUpdate, PasswordChange
from app.services.auth import AuthService
from app.database.db import get_db

router = APIRouter()
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

@router.post("/register", response_model=UserRead)
def register(user_in: UserCreate, db: Session = Depends(get_db)):
    user = AuthService.register(db, user_in)
    return user

@router.post("/login")
def login(form_data: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    user = AuthService.authenticate(db, form_data.username, form_data.password)
    token = AuthService.create_token(user.id)
    return {"access_token": token, "token_type": "bearer"}

@router.get("/me", response_model=UserRead)
def read_me(db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)):
    user = AuthService.get_current_user(db, token)
    return user

@router.patch("/me", response_model=UserRead)
def update_me(payload: UserUpdate, db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)):
    user = AuthService.get_current_user(db, token)
    updated = AuthService.update_user(db, user, payload)  # реализовать метод update_user в сервисе
    return updated

@router.post("/change-password")
def change_password(data: PasswordChange, db: Session = Depends(get_db), token: str = Depends(oauth2_scheme)):
    user = AuthService.get_current_user(db, token)
    AuthService.change_password(db, user, data)
    return {"ok": True}
