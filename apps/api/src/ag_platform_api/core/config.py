from functools import lru_cache

from pydantic import Field, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../../.env", ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "AG Platform API"
    api_v1_prefix: str = "/api/v1"
    environment: str = "development"
    database_url: str = "postgresql+asyncpg://agpay:agpay_postgres_dev@localhost:5432/agpay"
    redis_url: str = "redis://:agpay_redis_dev@localhost:6379/0"
    jwt_secret: str = "development-only-change-me-please-32-chars"
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    agent_token_expire_days: int = 365
    pairing_token_expire_minutes: int = 15
    agent_online_window_seconds: int = 120
    credential_encryption_key: str | None = None
    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:3000"])

    @field_validator("jwt_secret")
    @classmethod
    def validate_jwt_secret(cls, value: str) -> str:
        if len(value) < 32:
            raise ValueError("JWT_SECRET must contain at least 32 characters")
        return value

    @model_validator(mode="after")
    def validate_production_secrets(self) -> "Settings":
        if self.environment.lower() not in {"development", "test"}:
            if self.jwt_secret == "development-only-change-me-please-32-chars":
                raise ValueError("JWT_SECRET must be changed outside development")
            if self.credential_encryption_key is None:
                raise ValueError("CREDENTIAL_ENCRYPTION_KEY is required outside development")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
