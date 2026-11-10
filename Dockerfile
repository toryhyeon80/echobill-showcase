# EchoBill backend image — built from the REPO ROOT so configs/ ships with backend/.
# Railway: leave Root Directory empty (repo root); railway.json points here.
# Layout must stay /app/backend + /app/configs: parse_llm.py resolves prompts as
# <backend>/../configs/prompts/*, so a backend-only build would silently fall back to Rule.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install -r backend/requirements.txt

# Only these two dirs ship; .dockerignore drops .env, local DBs and tests inside them
COPY backend/ backend/
COPY configs/ configs/

RUN useradd --create-home --uid 10001 app && chown -R app /app
USER app

WORKDIR /app/backend
# Primary: railway.json preDeployCommand runs `alembic upgrade head` before this container starts
# (a failure aborts the deploy). Kept here as a safety net — a no-op once the DB is at head.
# If it fails the server never starts, /health fails and Railway keeps the old deploy.
# Railway injects PORT. exec → uvicorn gets SIGTERM directly.
CMD ["sh", "-c", "alembic upgrade head && exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
