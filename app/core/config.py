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
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 525600
    REFRESH_TOKEN_EXPIRE_DAYS: int = 365

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

    # AI Provider Selection
    AI_PROVIDER: str = "openai"  # Options: "openai", "google"

    # OpenAI Configuration
    OPENAI_API_KEY: str = Field(min_length=1)
    OPENAI_IMAGE_MODEL: str
    OPENAI_TEXT_MODEL: str

    # Google Gemini Configuration
    GOOGLE_API_KEY: str = ""  # Optional, only needed if using Google provider
    GOOGLE_TEXT_MODEL: str = "gemini-2.5-flash"  # Text model for vision analysis and text generation
    GOOGLE_IMAGE_MODEL: str = "imagen-4.0-generate-001"  # Image model for image generation
    GOOGLE_REFERENCE_IMAGE_MODEL: str = "imagen-4.0-generate-001"  # Image model for prompt + reference image

    # Character Generation Settings
    CHARACTER_IMAGE_SIZE: str
    CHARACTER_IMAGE_QUALITY: str
    CHARACTER_GENERATION_ENABLED: bool

    # Story Generation Settings
    STORY_TEXT_MODEL: str = "gpt-4o"
    STORY_IMAGE_MODEL: str = "dall-e-3"
    STORY_IMAGE_SIZE: str = "1024x1024"
    STORY_IMAGE_QUALITY: str = "standard"
    STORY_MAX_RETRIES: int = 3
    STORY_GENERATION_ENABLED: bool = True
    STORY_MOCK_LLM_RESPONSES: bool = False

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
