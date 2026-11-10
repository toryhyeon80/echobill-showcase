"""Server-Timing header (PM 10/15 속도 확인) — app time on every response, nothing else."""

import re

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app

_TIMING = re.compile(r"^app;dur=\d+\.\d$")


def _client(**overrides) -> TestClient:
    return TestClient(create_app(Settings(_env_file=None, app_env="development", **overrides)))


def test_health_carries_app_duration_only() -> None:
    res = _client().get("/health")
    assert res.status_code == 200
    assert _TIMING.match(res.headers["server-timing"])


def test_rejected_requests_are_timed_too() -> None:
    # outermost layer: an ORIGIN_FORBIDDEN 403 and a 404 still show how long the app took
    client = _client(origin_secret="s" * 32)
    assert _TIMING.match(client.get("/api/v1/auth/me").headers["server-timing"])
    assert _TIMING.match(_client().get("/no-such-path").headers["server-timing"])
