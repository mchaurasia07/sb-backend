from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from slowapi.errors import RateLimitExceeded

from app.core.config import settings
from app.core.exceptions import register_exception_handlers, rate_limit_handler
from app.core.logger import configure_logging, get_logger
from app.core.rate_limit import limiter
from app.middleware.auth import AuthenticationContextMiddleware
from app.middleware.request_context import RequestContextMiddleware
from app.routes.v1 import api_router

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Configure application resources on startup and shutdown."""
    configure_logging()
    logger.info("application_starting", app_name=settings.APP_NAME, environment=settings.ENVIRONMENT)
    yield
    logger.info("application_stopping", app_name=settings.APP_NAME)


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title=settings.APP_NAME,
        version="1.0.0",
        debug=settings.DEBUG,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    app.state.limiter = limiter
    app.add_exception_handler(RateLimitExceeded, rate_limit_handler)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuthenticationContextMiddleware)
    app.add_middleware(RequestContextMiddleware)

    register_exception_handlers(app)
    Path(settings.MEDIA_ROOT).mkdir(parents=True, exist_ok=True)
    app.mount(settings.MEDIA_URL_PREFIX, StaticFiles(directory=settings.MEDIA_ROOT), name="media")

    # Mount audio files directory for story narration
    Path("audio").mkdir(exist_ok=True)
    app.mount("/audio", StaticFiles(directory="audio"), name="audio")

    app.include_router(api_router, prefix=settings.API_V1_PREFIX)

    @app.get("/health", tags=["Health"])
    async def health_check() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()
