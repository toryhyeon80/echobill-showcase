"""STT counterparty hints — owner's own confirmed names only, off by default (PM 10/19)."""

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.main import app
from app.models import LedgerEntry
from app.services.stt import STTError, build_stt_prompt, echoes_hints, transcribe
from app.services.stt_hints import MAX_HINT_CHARS, recent_counterparty_names

T0 = datetime(2026, 10, 1, tzinfo=UTC)


def _entry(db: Session, user: str, name: str | None, *, status: str = "confirmed", minutes: int = 0,
           deleted: bool = False) -> None:
    db.add(LedgerEntry(
        entry_id=str(uuid.uuid4()), user_id=user, status=status, counterparty=name, total_amount=1000,
        created_at=T0 + timedelta(minutes=minutes), deleted_at=T0 if deleted else None,
    ))
    db.commit()


# --- prompt building -------------------------------------------------------------------------

def test_prompt_without_hints_is_the_base() -> None:
    assert build_stt_prompt("외상, 박스") == "외상, 박스"
    assert build_stt_prompt("") == ""


def test_hints_follow_the_base_on_their_own_line() -> None:
    assert build_stt_prompt("외상, 박스", ["가나상회", "다라식당"]) == "외상, 박스\n거래처: 가나상회, 다라식당"


def test_hints_alone_when_no_base_prompt() -> None:
    assert build_stt_prompt("", ["가나상회"]) == "거래처: 가나상회"


class _FakeTranscriptions:
    def __init__(self) -> None:
        self.request: dict | None = None
        self.text = "가나상회 배추 두 박스"

    def create(self, **kwargs):
        self.request = kwargs
        return SimpleNamespace(text=self.text)


def _settings(**overrides) -> Settings:
    return Settings(**{"app_env": "development", "openai_api_key": "test-key", "_env_file": None, **overrides})


def test_transcribe_sends_hints_as_prompt() -> None:
    t = _FakeTranscriptions()
    transcribe(b"audio", "a.webm", "audio/webm", counterparty_hints=["가나상회"], settings=_settings(),
               client=SimpleNamespace(audio=SimpleNamespace(transcriptions=t)))
    assert t.request["prompt"] == "거래처: 가나상회"


def test_transcribe_without_hints_sends_no_prompt() -> None:
    t = _FakeTranscriptions()
    transcribe(b"audio", "a.webm", "audio/webm", counterparty_hints=[], settings=_settings(),
               client=SimpleNamespace(audio=SimpleNamespace(transcriptions=t)))
    assert "prompt" not in t.request


# --- hint echo (PM 10/21 noise check) -------------------------------------------------------------

OWNER = ["영수네 가게", "영수네", "김밥천하", "철수씨", "철수씨 가게", "대박마트", "수산시장"]


@pytest.mark.parametrize("heard", [
    "거래처: 영수네 가게, 김밥천하, 철수네",            # the label itself
    "영수네 가게, 김밥천하, 대박마트, 수산시장",        # the list without the label
])
def test_a_transcript_repeating_the_hint_list_is_an_echo(heard: str) -> None:
    assert echoes_hints(heard, OWNER)


@pytest.mark.parametrize("heard", [
    "영수네 가게에 사과 한 박스 현금 이만 원",          # 영수네 inside 영수네 가게 counts once
    "철수씨 가게 마늘 한접 이만원 달아요",
    "대박마트랑 수산시장에 각각 외상 만 원씩",          # two names: multi-counterparty 400, not an echo
    "영수네 가게",                                    # one name alone stays (a voice answer can be just a name)
])
def test_real_speech_with_listed_names_is_not_an_echo(heard: str) -> None:
    assert not echoes_hints(heard, OWNER)


def test_no_hints_never_an_echo() -> None:
    assert not echoes_hints("거래처: 영수네 가게, 김밥천하, 대박마트", [])


def test_transcribe_turns_a_hint_echo_into_stt_error(caplog: pytest.LogCaptureFixture) -> None:
    t = _FakeTranscriptions()
    t.text = "거래처: 영수네 가게, 김밥천하, 철수씨, 대박마트"
    with pytest.raises(STTError):
        transcribe(b"audio", "a.webm", "audio/webm", counterparty_hints=OWNER, settings=_settings(),
                   client=SimpleNamespace(audio=SimpleNamespace(transcriptions=t)))
    assert "김밥천하" not in caplog.text  # names are never logged


# --- which names --------------------------------------------------------------------------------

def test_only_own_confirmed_live_names_most_recent_first(db: Session) -> None:
    _entry(db, "u1", "가나상회", minutes=1)
    _entry(db, "u1", "다라식당", minutes=5)
    _entry(db, "u1", "가나상회", minutes=9)  # used again → most recent
    _entry(db, "u1", "후보상회", status="candidate", minutes=10)  # could be misheard
    _entry(db, "u1", "거절상회", status="rejected", minutes=11)
    _entry(db, "u1", "삭제상회", deleted=True, minutes=12)
    _entry(db, "u1", None, minutes=13)
    _entry(db, "u2", "남의상회", minutes=14)  # another owner's data never leaks
    assert recent_counterparty_names(db, "u1", 30) == ["가나상회", "다라식당"]


def test_limit_and_zero_limit(db: Session) -> None:
    for i in range(5):
        _entry(db, "u1", f"상회{i}", minutes=i)
    assert recent_counterparty_names(db, "u1", 2) == ["상회4", "상회3"]
    assert recent_counterparty_names(db, "u1", 0) == []


def test_whitespace_is_collapsed(db: Session) -> None:
    _entry(db, "u1", " 가나\n상회 ")
    assert recent_counterparty_names(db, "u1", 30) == ["가나 상회"]


def test_joined_names_stay_under_the_char_cap(db: Session) -> None:
    for i in range(30):
        _entry(db, "u1", f"{i:02d}" + "가" * 30, minutes=i)
    names = recent_counterparty_names(db, "u1", 30)
    assert names
    assert len(", ".join(names)) <= MAX_HINT_CHARS


def test_one_overlong_recent_name_does_not_empty_the_hints(db: Session) -> None:
    """병철 #167 리뷰: a name over the cap at the front skipped every shorter name after it (break → continue)."""
    _entry(db, "u1", "가나상회", minutes=0)
    _entry(db, "u1", "다" * (MAX_HINT_CHARS + 1), minutes=1)
    assert recent_counterparty_names(db, "u1", 30) == ["가나상회"]


# --- /parse-audio wiring ----------------------------------------------------------------------

client = TestClient(app)


def _post_audio() -> int:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    res = client.post(
        "/api/v1/ledger/parse-audio",
        files={"file": ("a.webm", b"x" * 1000, "audio/webm")},
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())},
    )
    return res.status_code


def _capture(monkeypatch: pytest.MonkeyPatch) -> dict:
    seen: dict = {}

    def fake(*_a, counterparty_hints=(), **_k):
        seen["hints"] = list(counterparty_hints)
        return "가나상회에게 배추 3박스 외상 30000원"

    monkeypatch.setattr("app.api.ledger.transcribe", fake)
    return seen


def test_hints_off_by_default_sends_none(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    assert Settings(_env_file=None, app_env="development").stt_counterparty_hints is False
    _entry(db, "user-b", "가나상회")
    seen = _capture(monkeypatch)
    assert _post_audio() == 200
    assert seen["hints"] == []


def test_hints_on_sends_the_callers_own_names(db: Session, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(get_settings(), "stt_counterparty_hints", True)
    _entry(db, "user-b", "가나상회")
    _entry(db, "someone-else", "남의상회")
    seen = _capture(monkeypatch)
    assert _post_audio() == 200
    assert seen["hints"] == ["가나상회"]
