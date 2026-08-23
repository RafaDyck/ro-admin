"""Settings. Every secret is required -- there are no development fallbacks.

The predecessor fell back to 'dev-secret-key' and 'dev-jwt-secret' when the
environment was unset. A fallback secret in software other people install is
not a convenience; it is a shared, published credential. Refusing to start is
the correct behaviour.
"""
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RO_ADMIN_", env_file=".env")

    jwt_secret: str = Field(min_length=16)
    db_host: str = "127.0.0.1"
    db_port: int = 3306
    db_user: str
    db_password: str
    db_name: str = "ragnarok"
    md5_passwords: bool = False
    token_ttl_seconds: int = 3600

    @field_validator("jwt_secret")
    @classmethod
    def reject_placeholder_secrets(cls, v: str) -> str:
        banned = {"changeme", "secret", "dev-secret-key", "dev-jwt-secret"}
        if v.strip().lower() in banned:
            raise ValueError("jwt_secret is a known placeholder value")
        return v
