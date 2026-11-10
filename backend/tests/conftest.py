"""Shared test DB — in-memory SQLite (or TEST_DATABASE_URL), fresh tables per test."""

import os
from collections.abc import Generator

# before any app import — settings are cached; tests mint tokens via dev-token
os.environ["APP_ENV"] = "development"

import pytest  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import Session, sessionmaker  # noqa: E402
from sqlalchemy.pool import StaticPool  # noqa: E402

from app.db import get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import Base  # noqa: E402

# TEST_DATABASE_URL=postgresql://… runs the whole suite on PostgreSQL (CI), default in-memory SQLite
_TEST_DB_URL = os.environ.get("TEST_DATABASE_URL")
engine = (
    create_engine(_TEST_DB_URL)
    if _TEST_DB_URL
    else create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
)
TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def _override_get_db() -> Generator[Session, None, None]:
    db = TestingSession()
    try:
        yield db
    finally:
        db.close()


app.dependency_overrides[get_db] = _override_get_db


@pytest.fixture(autouse=True)
def _fresh_tables() -> Generator[None, None, None]:
    """Every test starts with empty tables."""
    Base.metadata.create_all(bind=engine)
    yield
    Base.metadata.drop_all(bind=engine)


@pytest.fixture
def db() -> Generator[Session, None, None]:
    """Direct session for asserting what was (not) stored."""
    session = TestingSession()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def session_factory() -> sessionmaker:
    """Open extra sessions (e.g. a concurrent transaction) on the test DB without re-importing conftest."""
    return TestingSession
