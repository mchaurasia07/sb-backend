from functools import cached_property
from pathlib import Path

from pydantic import AnyUrl, Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven application settings."""

    APP_NAME: str
    ENVIRONMENT: str
    DEBUG: bool = Field(validation_alias="APP_DEBUG")
    API_V1_PREFIX: str

    DATABASE_URL: str
    DB_POOL_PRE_PING: bool
    DB_POOL_SIZE: int
    DB_MAX_OVERFLOW: int
    DB_POOL_RECYCLE_SECONDS: int

    JWT_SECRET_KEY: str = Field(min_length=32)
    JWT_ALGORITHM: str
    ACCESS_TOKEN_EXPIRE_MINUTES: int
    REFRESH_TOKEN_EXPIRE_DAYS: int

    BCRYPT_ROUNDS: int
    OTP_EXPIRE_MINUTES: int
    OTP_LENGTH: int
    MAX_LOGIN_ATTEMPTS: int
    ACCOUNT_LOCK_MINUTES: int

    GOOGLE_CLIENT_ID: str
    BACKEND_CORS_ORIGINS: str
    RATE_LIMIT_DEFAULT: str

    SMTP_HOST: str
    SMTP_PORT: int
    SMTP_USERNAME: str
    SMTP_PASSWORD: str
    SMTP_FROM_EMAIL: str
    SMTP_FROM_NAME: str
    SMTP_USE_TLS: bool
    SMTP_TIMEOUT_SECONDS: float

    # Storage Configuration - supports both relative and absolute paths
    STORAGE_BASE_PATH: str = Field(default=".", description="Base path used when storage roots are relative")
    MEDIA_ROOT: str = Field(description="Absolute or relative path for image storage")
    MEDIA_URL_PREFIX: str
    IMAGE_MAX_UPLOAD_BYTES: int

    # Audio Storage Configuration
    AUDIO_ROOT: str = Field(description="Absolute or relative path for audio storage")
    AUDIO_URL_PREFIX: str

    # AI Provider Selection
    AI_PROVIDER: str  # Options: "openai", "google"

    # OpenAI Configuration
    OPENAI_API_KEY: str
    OPENAI_IMAGE_MODEL: str
    OPENAI_TEXT_MODEL: str

    # Google Gemini Configuration
    GOOGLE_API_KEY: str  # Optional, only needed if using Google provider
    GOOGLE_TEXT_MODEL: str  # Text model for vision analysis and text generation
    GOOGLE_IMAGE_MODEL: str  # Image model for image generation
    GOOGLE_REFERENCE_IMAGE_MODEL: str  # Image model for prompt + reference image
    GOOGLE_TTS_MODEL: str
    GOOGLE_TTS_VOICE: str
    GOOGLE_TTS_SKIP_CALL: bool

    # Character Generation Settings
    CHARACTER_IMAGE_SIZE: str
    CHARACTER_IMAGE_QUALITY: str
    CHARACTER_GENERATION_ENABLED: bool

    # Story Generation Settings
    STORY_TEXT_MODEL: str
    STORY_IMAGE_MODEL: str
    STORY_IMAGE_SIZE: str
    STORY_COVER_IMAGE_SIZE: str
    STORY_PAGE_IMAGE_SIZE: str
    STORY_BACK_COVER_IMAGE_SIZE: str
    STORY_COVER_ASPECT_RATIO: str
    STORY_PAGE_ASPECT_RATIO: str
    STORY_BACK_COVER_ASPECT_RATIO: str
    STORY_IMAGE_QUALITY: str
    STORY_MAX_RETRIES: int
    STORY_GENERATION_ENABLED: bool
    STORY_MOCK_LLM_RESPONSES: bool

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @classmethod
    def settings_customise_sources(
        cls,
        settings_cls: type[BaseSettings],
        init_settings: PydanticBaseSettingsSource,
        env_settings: PydanticBaseSettingsSource,
        dotenv_settings: PydanticBaseSettingsSource,
        file_secret_settings: PydanticBaseSettingsSource,
    ) -> tuple[PydanticBaseSettingsSource, ...]:
        return dotenv_settings, env_settings, init_settings, file_secret_settings

    @field_validator("DATABASE_URL")
    @classmethod
    def validate_async_database_url(cls, value: str) -> str:
        if not value.startswith("mysql+asyncmy://"):
            raise ValueError("DATABASE_URL must use mysql+asyncmy://")
        return value

    @field_validator("SMTP_FROM_EMAIL")
    @classmethod
    def set_default_from_email(cls, value: str, info) -> str:
        return value or info.data.get("SMTP_USERNAME", "")

    @cached_property
    def cors_origins(self) -> list[str | AnyUrl]:
        return [origin.strip() for origin in self.BACKEND_CORS_ORIGINS.split(",") if origin.strip()]

    @cached_property
    def storage_base_path(self) -> Path:
        return Path(self.STORAGE_BASE_PATH).expanduser().resolve()

    @cached_property
    def media_root_path(self) -> Path:
        return self._resolve_storage_path(self.MEDIA_ROOT)

    @cached_property
    def audio_root_path(self) -> Path:
        return self._resolve_storage_path(self.AUDIO_ROOT)

    def _resolve_storage_path(self, configured_path: str) -> Path:
        path = Path(configured_path).expanduser()
        if path.is_absolute():
            return path.resolve()
        return (self.storage_base_path / path).resolve()


settings = Settings()  # type: ignore[call-arg]
