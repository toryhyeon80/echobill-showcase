"""Deploy layout guard — the Railway image must ship configs/ next to backend/.

parse_llm resolves prompts as <backend>/../configs/, so a backend-only image loads
no prompt and every request silently falls back to Rule (still HTTP 200).
Docker is not run in CI, so this checks the build files themselves.
"""

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _lines(name: str) -> list[str]:
    text = (REPO_ROOT / name).read_text(encoding="utf-8")
    return [ln.strip() for ln in text.splitlines() if ln.strip() and not ln.strip().startswith("#")]


def test_configs_dir_exists_at_repo_root() -> None:
    """COPY configs/ fails the build if the directory is missing."""
    assert (REPO_ROOT / "configs").is_dir()


def test_dockerfile_copies_backend_and_configs_as_siblings() -> None:
    lines = _lines("Dockerfile")
    assert "COPY backend/ backend/" in lines
    assert "COPY configs/ configs/" in lines
    # both land under WORKDIR /app → /app/backend and /app/configs
    assert lines.index("WORKDIR /app") < lines.index("COPY backend/ backend/")


def test_dockerignore_keeps_secrets_out() -> None:
    lines = _lines(".dockerignore")
    for pattern in ("**/.env", "**/.env.*", "**/*.db"):
        assert pattern in lines
    # configs/ and backend/ must not be ignored
    assert not any(ln.rstrip("/") in ("configs", "backend", "*") for ln in lines)


def test_railway_uses_root_dockerfile() -> None:
    import json

    cfg = json.loads((REPO_ROOT / "railway.json").read_text(encoding="utf-8"))
    assert cfg["build"]["builder"] == "DOCKERFILE"
    assert cfg["build"]["dockerfilePath"] == "Dockerfile"
    assert "configs/**" in cfg["build"]["watchPatterns"]


def test_postgres_driver_installed() -> None:
    """Railway gives a plain postgresql:// URL — its default driver must be in requirements."""
    from sqlalchemy import create_engine

    engine = create_engine("postgresql://user:pw@localhost/db")  # no connection is opened
    assert engine.dialect.driver == "psycopg"


def test_container_migrates_before_serving() -> None:
    """Railway PostgreSQL has no create_all — the image must run Alembic before uvicorn."""
    cmd = next(ln for ln in _lines("Dockerfile") if ln.startswith("CMD"))
    assert cmd.index("alembic upgrade head") < cmd.index("uvicorn")
    assert "&&" in cmd  # a failed migration must stop the server from starting
    assert (REPO_ROOT / "backend" / "alembic.ini").is_file()


def test_railway_runs_migrations_before_deploy() -> None:
    """preDeployCommand: a failed migration aborts the deploy instead of serving on a broken schema."""
    import json

    cfg = json.loads((REPO_ROOT / "railway.json").read_text(encoding="utf-8"))
    assert cfg["deploy"]["preDeployCommand"] == ["alembic upgrade head"]
