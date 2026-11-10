"""Aggregate API router."""

from fastapi import APIRouter

from app.api import auth, health, ledger, me, tax, unpaid, voice, voice_answer

api_router = APIRouter()
api_router.include_router(health.router)
api_router.include_router(auth.router, prefix="/api/v1/auth", tags=["auth"])
api_router.include_router(me.router, prefix="/api/v1/me", tags=["auth"])
# list · counterparty first: static paths must not be shadowed by /{entry_id}
api_router.include_router(unpaid.ledger_router, prefix="/api/v1/ledger", tags=["ledger"])
api_router.include_router(ledger.router, prefix="/api/v1/ledger", tags=["ledger"])
api_router.include_router(voice_answer.router, prefix="/api/v1/ledger", tags=["ledger"])
api_router.include_router(unpaid.dashboard_router, prefix="/api/v1/dashboard", tags=["unpaid"])
api_router.include_router(unpaid.payments_router, prefix="/api/v1/payments", tags=["unpaid"])
api_router.include_router(tax.router, prefix="/api/v1/tax", tags=["tax"])
api_router.include_router(voice.router, prefix="/api/v1/voice", tags=["voice"])
