"""EchoBill FastAPI entrypoint — health, auth stub, ledger parse mock."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.config import Settings, get_settings
from app.middleware import (
    BodySizeLimitMiddleware,
    ErrorLogMiddleware,
    OriginSecretMiddleware,
    ServerErrorResponseMiddleware,
    ServerTimingMiddleware,
)
from app.db import init_db
from app.services.postprocess import unit_price_rules_active

logger = logging.getLogger(__name__)


def warn_inactive_postprocess(settings: Settings) -> None:
    """Log once at startup when LLM_POSTPROCESS is on but the unit-price rule is skipped (normalization off)."""
    if settings.llm_postprocess and not unit_price_rules_active(settings.llm_postprocess, settings.ko_numeral_normalize):
        logger.warning("KO_NUMERAL_NORMALIZE=false — 후처리 단가 규칙은 꺼지고 외상 규칙만 적용됨")


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Create tables on startup for local SQLite only; hosted PostgreSQL is migrated by Alembic (Dockerfile CMD)."""
    if get_settings().database_url.startswith("sqlite"):
        init_db()
    yield


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build FastAPI app with CORS and v1 routes. API docs are served in development only."""
    settings = settings or get_settings()
    warn_inactive_postprocess(settings)
    docs = settings.api_docs_enabled
    app = FastAPI(
        title=settings.app_name,
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs" if docs else None,
        redoc_url="/redoc" if docs else None,
        openapi_url="/openapi.json" if docs else None,
        description="EchoBill MVP scaffold — candidate-only parse mock; no confirmed without user feedback.",
    )
    # added before CORS → CORS is the outer layer, so a 413 still carries CORS headers
    app.add_middleware(
        BodySizeLimitMiddleware,
        default_limit=settings.max_body_bytes,
        path_limits={
            "/api/v1/ledger/parse-audio": settings.max_audio_body_bytes,
            "/api/v1/ledger/voice-answer": settings.max_audio_body_bytes,
        },
    )
    # between size limit and CORS: sees 413s, and CORS still wraps every response
    app.add_middleware(ErrorLogMiddleware)
    # right inside CORS: an unhandled exception still answers a 500 the browser can read
    app.add_middleware(ServerErrorResponseMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # outermost: a request that skipped Cloudflare is refused before anything else runs (off when unset)
    app.add_middleware(
        OriginSecretMiddleware, secret=settings.origin_secret.get_secret_value(), header=settings.origin_secret_header
    )
    # very outermost: Server-Timing covers every layer above, so header time = whole app time (PM 10/15)
    app.add_middleware(ServerTimingMiddleware)
    app.include_router(api_router)
    return app


app = create_app()
