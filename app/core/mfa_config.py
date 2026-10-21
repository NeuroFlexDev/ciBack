from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class MFASettings(BaseSettings):
    # Remains off until the owner personally enrolls and verifies their recovery codes.
    MFA_REQUIRED_FOR_OPERATORS: bool = False
    MFA_STEP_UP_SECONDS: int = Field(default=600, ge=60, le=900)
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')


mfa_settings = MFASettings()
