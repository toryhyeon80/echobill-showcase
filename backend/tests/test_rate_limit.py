"""Per-user caps on /parse·/parse-audio (PM #14) and the 24h idempotency purge."""

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import get_settings
from app.main import app
from app.models import IdempotencyRecord, LedgerEntry
from app.services.idempotency import purge_expired

client = TestClient(app)
TEXT = "김씨에게 배추 3박스 외상 30000원"


def _token(user_id: str = "user-b") -> str:
    return client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]


def _parse(user_id: str = "user-b", key: str | None = None, text: str = TEXT):
    return client.post(
        "/api/v1/ledger/parse",
        json={"utterance_text": text},
        headers={"Authorization": f"Bearer {_token(user_id)}", "X-Idempotency-Key": key or str(uuid.uuid4())},
    )


@pytest.fixture
def limits(monkeypatch: pytest.MonkeyPatch):
    def set_limits(per_minute: int = 0, per_day: int = 0, audio_per_minute: int = 0, audio_per_day: int = 0) -> None:
        monkeypatch.setattr(get_settings(), "parse_limit_per_minute", per_minute)
        monkeypatch.setattr(get_settings(), "parse_limit_per_day", per_day)
        monkeypatch.setattr(get_settings(), "audio_limit_per_minute", audio_per_minute)
        monkeypatch.setattr(get_settings(), "audio_limit_per_day", audio_per_day)

    return set_limits


@pytest.fixture
def fake_stt(monkeypatch: pytest.MonkeyPatch):
    """Whisper replaced by a fixed transcript so /parse-audio succeeds offline."""
    monkeypatch.setattr("app.api.ledger.transcribe", lambda *a, **k: TEXT)


def _audio(user_id: str = "user-b"):
    return client.post(
        "/api/v1/ledger/parse-audio",
        files={"file": ("a.webm", b"x" * 1000, "audio/webm")},
        headers={"Authorization": f"Bearer {_token(user_id)}", "X-Idempotency-Key": str(uuid.uuid4())},
    )


def test_off_by_default(db: Session) -> None:
    """No numbers from D09 yet → no cap."""
    for _ in range(5):
        assert _parse().status_code == 200


def test_minute_cap_returns_429_with_retry_after(db: Session, limits) -> None:
    limits(per_minute=2)
    assert _parse().status_code == 200
    assert _parse().status_code == 200
    res = _parse()
    assert res.status_code == 429
    assert res.json()["error_code"] == "RATE_LIMITED"
    assert 1 <= int(res.headers["Retry-After"]) <= 60
    assert db.query(LedgerEntry).count() == 2


def test_400_attempts_count_too(db: Session, limits) -> None:
    """Every LLM call costs money, valid or not."""
    limits(per_minute=1)
    assert _parse(text="김씨에게 배추 외상").status_code == 400
    assert _parse().status_code == 429


def test_replay_of_same_key_is_not_limited(db: Session, limits) -> None:
    limits(per_minute=1)
    key = str(uuid.uuid4())
    first = _parse(key=key)
    again = _parse(key=key)
    assert first.status_code == again.status_code == 200
    assert first.json() == again.json()


def test_caps_are_per_user(db: Session, limits) -> None:
    limits(per_minute=1)
    assert _parse("user-a").status_code == 200
    assert _parse("user-b").status_code == 200
    assert _parse("user-a").status_code == 429


def test_day_cap_counts_older_than_a_minute(db: Session, limits) -> None:
    limits(per_day=2)
    assert _parse().status_code == 200
    db.query(IdempotencyRecord).update({"created_at": datetime.now(timezone.utc) - timedelta(hours=3)})
    db.commit()
    assert _parse().status_code == 200
    res = _parse()
    assert res.status_code == 429
    assert int(res.headers["Retry-After"]) > 3600  # oldest frees up in ~21h


def test_other_endpoints_are_not_capped(db: Session, limits) -> None:
    limits(per_minute=1)
    entry_id = _parse().json()["entry_id"]
    res = client.post(
        f"/api/v1/ledger/{entry_id}/feedback", json={"action": "confirm"},
        headers={"Authorization": f"Bearer {_token()}", "X-Idempotency-Key": str(uuid.uuid4())},
    )
    assert res.status_code == 200


def test_scope_is_stored_without_ids(db: Session) -> None:
    entry_id = _parse().json()["entry_id"]
    client.post(
        f"/api/v1/ledger/{entry_id}/feedback", json={"action": "confirm"},
        headers={"Authorization": f"Bearer {_token()}", "X-Idempotency-Key": str(uuid.uuid4())},
    )
    assert sorted(r.scope for r in db.query(IdempotencyRecord)) == ["feedback", "parse"]


def test_purge_removes_only_records_past_24h(db: Session) -> None:
    _parse()
    _parse()
    old = db.query(IdempotencyRecord).first()
    old.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
    db.commit()
    assert purge_expired(db, get_settings()) == 1
    assert db.query(IdempotencyRecord).count() == 1


def test_parallel_burst_cannot_exceed_cap(db: Session, limits, session_factory) -> None:
    """Rows saved by in-flight requests count: a 4th reservation over a cap of 3 is refused."""
    from app.config import get_settings as gs
    from app.services import rate_limit

    limits(per_minute=3)
    now = datetime.now(timezone.utc)
    for i in range(4):  # four reservations already saved (e.g. parallel requests mid-flight)
        db.add(IdempotencyRecord(user_id="user-b", key=f"k{i}", request_hash="h", scope="parse", created_at=now))
    db.commit()
    assert rate_limit.check(db, "user-b", "parse", gs()) is not None


def test_refused_request_leaves_no_reservation(db: Session, limits) -> None:
    limits(per_minute=1)
    _parse()
    assert _parse().status_code == 429
    assert db.query(IdempotencyRecord).count() == 1


def test_purge_failure_does_not_fail_the_request(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    import app.services.idempotency as idem

    def broken(*_args, **_kwargs):
        raise RuntimeError("lock timeout")

    monkeypatch.setattr(idem, "purge_expired", broken)
    monkeypatch.setattr(idem, "_last_purge", 0.0)
    assert _parse().status_code == 200


def test_purge_keeps_a_day_even_if_ttl_is_shorter(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    """The daily cap needs 24h of history."""
    monkeypatch.setattr(get_settings(), "idempotency_ttl_hours", 1)
    _parse()
    db.query(IdempotencyRecord).update({"created_at": datetime.now(timezone.utc) - timedelta(hours=5)})
    db.commit()
    assert purge_expired(db, get_settings()) == 0


def test_audio_has_its_own_cap(db: Session, limits, fake_stt) -> None:
    """AUDIO_LIMIT_* caps /parse-audio; the text cap does not apply to it (PM 10/6)."""
    limits(per_minute=1, audio_per_minute=2)
    assert _audio().status_code == 200
    assert _audio().status_code == 200
    res = _audio()
    assert res.status_code == 429
    assert res.json()["error_code"] == "RATE_LIMITED"


def test_voice_does_not_use_the_text_budget(db: Session, limits, fake_stt) -> None:
    limits(per_minute=1, audio_per_minute=5)
    assert _audio().status_code == 200
    assert _audio().status_code == 200
    assert _parse().status_code == 200  # text budget untouched by voice
    assert _parse().status_code == 429


def test_text_does_not_use_the_voice_budget(db: Session, limits, fake_stt) -> None:
    limits(per_minute=5, audio_per_minute=1)
    assert _parse().status_code == 200
    assert _parse().status_code == 200
    assert _audio().status_code == 200
    assert _audio().status_code == 429
