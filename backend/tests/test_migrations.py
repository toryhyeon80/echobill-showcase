"""Alembic guard — migrations must build exactly the schema in app.models.

A model change without a new revision fails here, before it reaches Railway.
Runs on SQLite always; on PostgreSQL too when TEST_POSTGRES_URL is set (CI service).
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, inspect, text

from app.models import Base

BACKEND = Path(__file__).resolve().parents[1]

_targets = ["sqlite"]
if os.environ.get("TEST_POSTGRES_URL"):
    _targets.append("postgres")


def _config(url: str) -> Config:
    cfg = Config(str(BACKEND / "alembic.ini"))
    cfg.set_main_option("script_location", str(BACKEND / "migrations"))
    cfg.set_main_option("sqlalchemy.url", url)
    return cfg


@pytest.fixture(params=_targets)
def db_url(request: pytest.FixtureRequest, tmp_path: Path):
    """Empty database per test; PostgreSQL is wiped before and after."""
    if request.param == "sqlite":
        yield f"sqlite:///{tmp_path / 'migrate.db'}"
        return
    url = os.environ["TEST_POSTGRES_URL"]
    engine = create_engine(url)

    def wipe() -> None:
        with engine.begin() as conn:
            conn.execute(text("DROP SCHEMA public CASCADE"))
            conn.execute(text("CREATE SCHEMA public"))

    wipe()
    yield url
    wipe()
    engine.dispose()


def _drift(url: str) -> list:
    engine = create_engine(url)
    try:
        with engine.connect() as conn:
            ctx = MigrationContext.configure(conn, opts={"compare_type": True})
            return compare_metadata(ctx, Base.metadata)
    finally:
        engine.dispose()


def test_upgrade_head_matches_models(db_url: str) -> None:
    command.upgrade(_config(db_url), "head")
    assert _drift(db_url) == []


def test_downgrade_and_upgrade_again(db_url: str) -> None:
    cfg = _config(db_url)
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")
    engine = create_engine(db_url)
    try:
        tables = set(inspect(engine).get_table_names()) - {"alembic_version"}
    finally:
        engine.dispose()
    assert tables == set()
    command.upgrade(cfg, "head")
    assert _drift(db_url) == []


def test_single_head() -> None:
    """Two branches each adding a revision must be merged before deploy."""
    heads = ScriptDirectory.from_config(_config("sqlite://")).get_heads()
    assert len(heads) == 1, f"multiple alembic heads: {heads}"
