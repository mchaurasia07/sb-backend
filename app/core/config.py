from functools import cached_property

from pydantic import AnyUrl, Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven application settings."""

    APP_NAME: str = "SB Backend"
    ENVIRONMENT: str = "local"
    DEBUG: bool = False
    API_V1_PREFIX: str = "/api/v1"

    DATABASE_URL: str

    JWT_SECRET_KEY: str = Field(min_length=32)
    JWT_ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 30

    BCRYPT_ROUNDS: int = 12
    OTP_EXPIRE_MINUTES: int = 10
    OTP_LENGTH: int = 6
    MAX_LOGIN_ATTEMPTS: int = 5
    ACCOUNT_LOCK_MINUTES: int = 15

    GOOGLE_CLIENT_ID: str
    BACKEND_CORS_ORIGINS: str = "http://localhost:3000"
    RATE_LIMIT_DEFAULT: str = "100/minute"

    SMTP_HOST: str = "smtp.gmail.com"
    SMTP_PORT: int = 587
    SMTP_USERNAME: str = ""
    SMTP_PASSWORD: str = ""
    SMTP_FROM_EMAIL: str = ""
    SMTP_FROM_NAME: str = "Storybook"
    SMTP_USE_TLS: bool = True
    SMTP_TIMEOUT_SECONDS: float = 10.0

    MEDIA_ROOT: str = "photo"
    MEDIA_URL_PREFIX: str = "/photo"
    IMAGE_MAX_UPLOAD_BYTES: int = 5 * 1024 * 1024

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_async_database_url(cls, value: str) -> str:
        if not value.startswith("mysql+aiomysql://"):
            raise ValueError("DATABASE_URL must use mysql+aiomysql://")
        return value

    @field_validator("SMTP_FROM_EMAIL")
    @classmethod
    def set_default_from_email(cls, value: str, info) -> str:
        return value or info.data.get("SMTP_USERNAME", "")

    @cached_property
    def cors_origins(self) -> list[str | AnyUrl]:
        return [origin.strip() for origin in self.BACKEND_CORS_ORIGINS.split(",") if origin.strip()]


settings = Settings()  # type: ignore[call-arg]
