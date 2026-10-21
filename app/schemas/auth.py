import re
from pydantic import BaseModel, Field, StrictBool, field_validator


class AuthCredentials(BaseModel):
    email: str = Field(..., min_length=5, max_length=255)
    password: str = Field(..., min_length=8, max_length=128)

    @field_validator("email")
    @classmethod
    def valid_email(cls, value):
        value = value.strip().lower()
        if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", value):
            raise ValueError("Укажите корректный email")
        return value


class RegistrationCredentials(AuthCredentials):
    full_name: str = Field(default="", max_length=160)
    terms_accepted: StrictBool
    legal_version: str = Field(min_length=1, max_length=32)
    marketing_accepted: StrictBool = False

    @field_validator("terms_accepted")
    @classmethod
    def accepted_terms(cls, value):
        if not value:
            raise ValueError("Примите пользовательское соглашение")
        return value


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str = ""


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse
