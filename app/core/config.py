from functools import cached_property
from pathlib import Path

from pydantic import AnyUrl, Field, field_validator
from pydantic_settings import BaseSettings, PydanticBaseSettingsSource, SettingsConfigDict


class Settings(BaseSettings):
    """Environment-driven application settings."""

    APP_NAME: str
    ENVIRONMENT: str
    DEBUG: bool = Field(validation_alias="APP_DEBUG")
    LOG_LEVEL: str = "INFO"
    API_V1_PREFIX: str

    DATABASE_URL: str
    SQL_ECHO: bool = False
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

    EXPO_PUSH_ACCESS_TOKEN: str = ""
    NOTIFICATION_ADMIN_TOKEN: str = ""

    # Storage Configuration - supports both relative and absolute paths
    MEDIA_ROOT: str = Field(description="Absolute or relative path for image storage")
    MEDIA_URL_PREFIX: str
    IMAGE_MAX_UPLOAD_BYTES: int
    IMAGE_STORAGE_PROVIDER: str = "r2"

    # Cloudflare R2 image storage.
    CLOUDFLARE_R2_ACCOUNT_ID: str = ""
    CLOUDFLARE_R2_ACCESS_KEY_ID: str = ""
    CLOUDFLARE_R2_SECRET_ACCESS_KEY: str = ""
    CLOUDFLARE_R2_BUCKET_NAME: str = ""
    CLOUDFLARE_R2_PUBLIC_BASE_URL: str = ""
    CLOUDFLARE_R2_IMAGE_KEY_PREFIX: str = "photo"
    CLOUDFLARE_R2_REGION: str = "auto"
    CLOUDFLARE_R2_CACHE_CONTROL: str = "public, max-age=31536000, immutable"

    # Audio Storage Configuration
    AUDIO_ROOT: str = Field(description="Absolute or relative path for audio storage")
    AUDIO_URL_PREFIX: str
    AUDIO_STORAGE_PROVIDER: str = "local"
    CLOUDFLARE_R2_AUDIO_KEY_PREFIX: str = "audio"

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
    STORY_BATCH_MAX_IMAGE_RETRIES: int = 3
    STORY_BATCH_MAX_AUDIO_RETRIES: int = 3
    STORY_BATCH_POLL_INTERVAL_SECONDS: int = 30
    STORY_BATCH_MAX_WAIT_SECONDS: int = 86400
    STORY_BATCH_RECONCILE_SCHEDULER_ENABLED: bool = True
    STORY_BATCH_RECONCILE_INTERVAL_SECONDS: int = 1800
    STORY_BATCH_RECONCILE_LIMIT: int = 50

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
    def media_root_path(self) -> Path:
        return Path(self.MEDIA_ROOT).expanduser().resolve()

    @cached_property
    def audio_root_path(self) -> Path:
        return Path(self.AUDIO_ROOT).expanduser().resolve()

    @cached_property
    def cloudflare_r2_endpoint_url(self) -> str:
        return f"https://{self.CLOUDFLARE_R2_ACCOUNT_ID}.r2.cloudflarestorage.com"

    @cached_property
    def cloudflare_r2_image_key_prefix(self) -> str:
        return self.CLOUDFLARE_R2_IMAGE_KEY_PREFIX.strip("/")


settings = Settings()  # type: ignore[call-arg]
