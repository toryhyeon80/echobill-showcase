"""Health check endpoints."""

from fastapi import APIRouter

from app.config import get_settings

router = APIRouter(tags=["health"])


@router.get("/health")
def health() -> dict[str, str]:
    """Liveness probe for local/Railway."""
    settings = get_settings()
    return {"status": "ok", "service": settings.app_name, "env": settings.app_env}
