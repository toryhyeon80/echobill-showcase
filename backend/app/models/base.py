"""ORM declarative base and shared column types."""

from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase

# JSONB on PostgreSQL (operational DB), plain JSON on SQLite (tests · local)
JSONType = JSON().with_variant(JSONB(), "postgresql")


class Base(DeclarativeBase):
    """Shared SQLAlchemy declarative base."""
