import re
from pydantic import BaseModel, Field, StrictBool, field_validator, ConfigDict


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


def valid_username(value: str) -> str:
    value = value.strip().lower()
    if not re.fullmatch(r'[a-z0-9][a-z0-9._-]{2,63}', value):
        raise ValueError('Логин: от 3 до 64 латинских букв, цифр, точек, дефисов или подчёркиваний')
    return value


class LoginCredentials(BaseModel):
    # Keep the existing wire field compatible with email-only clients.
    email: str = Field(min_length=3, max_length=255)
    password: str = Field(min_length=8, max_length=128)

    @field_validator('email')
    @classmethod
    def identity(cls, value):
        return AuthCredentials.valid_email(value) if '@' in value else valid_username(value)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    email: str | None
    username: str | None = None
    full_name: str = ""
    is_admin: bool = False
    organization_id: int | None = None
    organization_role: str = "member"
    organization_kind: str | None = None


class AuthResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class MFAChallengeResponse(BaseModel):
    mfa_required: bool = True
    challenge_token: str
    expires_at: str
