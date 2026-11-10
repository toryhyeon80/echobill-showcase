"""Ops metrics (PM #18) — fallback rate, latency, per-field correction, log error counts, no PII."""

import logging
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import FeedbackEvent, LedgerEntry
from app.services.ops_metrics import (
    compute, count_log_errors, count_voice_answers, default_periods, percentile, render,
)
from scripts.ops_metrics import run

client = TestClient(app)
NOW = datetime(2026, 10, 6, 3, 0, tzinfo=timezone.utc)  # KST 10/6 12:00


def _entry(db: Session, *, parser: str = "llm", fallback: bool = False, ms: int = 1000,
           created: datetime = NOW, counterparty: str = "김씨비밀상회") -> LedgerEntry:
    e = LedgerEntry(entry_id=str(uuid.uuid4()), user_id="user-b", status="candidate", counterparty=counterparty,
                    items=[], total_amount=1000, credit=True, utterance_text="김씨비밀상회 원문 발화",
                    parser_type=parser, is_fallback_used=fallback, processing_time_ms=ms, created_at=created)
    db.add(e)
    db.commit()
    return e


def _feedback(db: Session, entry: LedgerEntry, action: str, diff: dict | None = None,
              created: datetime = NOW) -> None:
    db.add(FeedbackEvent(user_id="user-b", ledger_entry_id=entry.id, action=action,
                         corrected_data=diff, created_at=created))
    db.commit()


def _today(db: Session):
    label, start, end = default_periods(NOW)[0]
    return compute(db, label, start, end)


def test_percentile_nearest_rank() -> None:
    assert percentile([], 95) is None
    assert percentile(list(range(1, 21)), 95) == 19
    assert percentile([5, 1, 3], 50) == 3


def test_fallback_rate_counts_only_llm_attempts(db: Session) -> None:
    _entry(db, parser="llm")
    _entry(db, parser="llm")
    _entry(db, parser="llm")
    _entry(db, parser="mock", fallback=True)
    _entry(db, parser="mock")  # LLM disabled → rule only, not an attempt
    _entry(db, parser="manual")
    m = _today(db)
    assert (m.candidates, m.llm_attempts, m.llm_fallbacks, m.rule_only, m.manual) == (6, 4, 1, 1, 1)
    assert m.fallback_rate == 0.25


def test_latency_excludes_manual(db: Session) -> None:
    for ms in (100, 200, 300, 400):
        _entry(db, ms=ms)
    _entry(db, parser="manual", ms=99999)
    m = _today(db)
    assert (m.llm_p50_ms, m.llm_p95_ms) == (200, 400)
    assert m.rule_p50_ms is None


def test_field_correction_rate_and_item_breakdown(db: Session) -> None:
    e = _entry(db)
    _feedback(db, e, "confirm")
    _feedback(db, e, "correct", {"counterparty": {"before": "김", "after": "김씨"}})
    _feedback(db, e, "correct", {
        "items": {"before": [{"name": "배추", "qty": "3", "amount": 30000}],
                  "after": [{"name": "배추", "qty": "4", "amount": 40000}]},
        "total_amount": {"before": 30000, "after": 40000},
    })
    _feedback(db, e, "reject")
    m = _today(db)
    assert (m.decisions, m.rejects) == (3, 1)
    assert m.correction_rate("counterparty") == pytest.approx(1 / 3)
    assert m.correction_rate("items") == pytest.approx(1 / 3)
    assert m.correction_rate("credit") == 0
    assert m.item_key_changes["qty"] == 1 and m.item_key_changes["amount"] == 1
    assert m.item_key_changes["name"] == 0


def test_periods_use_kst_days(db: Session) -> None:
    """KST 10/5 23:59 is not 'today' (10/6) but is in the last 7 days."""
    _entry(db, created=datetime(2026, 10, 5, 14, 59, tzinfo=timezone.utc))
    _entry(db, created=NOW - timedelta(days=10))  # outside both
    _entry(db)
    today_label, t0, t1 = default_periods(NOW)[0]
    week_label, w0, w1 = default_periods(NOW)[1]
    assert compute(db, today_label, t0, t1).candidates == 1
    assert compute(db, week_label, w0, w1).candidates == 2


def test_rule_only_latency_is_reported_apart(db: Session) -> None:
    """Rule-only ≈ 0ms must not drag the LLM percentile down (week-6 comparison)."""
    for ms in (1500, 1600):
        _entry(db, ms=ms)
    for _ in range(5):
        _entry(db, parser="mock", ms=0)
    m = _today(db)
    assert (m.llm_p50_ms, m.llm_p95_ms) == (1500, 1600)
    assert (m.rule_p50_ms, m.rule_p95_ms) == (0, 0)


def test_log_error_counting() -> None:
    lines = [
        "WARNING app.middleware api_error method=POST route=/api/v1/ledger/parse status=400 error_code=MISSING_REQUIRED_FIELD",
        "WARNING app.middleware api_error method=POST route=/api/v1/ledger/parse status=400 error_code=MISSING_REQUIRED_FIELD",
        "WARNING app.middleware api_error method=POST route=/api/v1/ledger/parse-audio status=504 error_code=EXTERNAL_API_TIMEOUT",
        "INFO something else",
    ]
    counts = count_log_errors(lines)
    assert counts[("POST /api/v1/ledger/parse", 400, "MISSING_REQUIRED_FIELD")] == 2
    assert counts[("POST /api/v1/ledger/parse-audio", 504, "EXTERNAL_API_TIMEOUT")] == 1


def test_external_failure_total_counts_old_504_logs_with_503() -> None:
    """DL-33: the log window can span the change, so the total line holds both codes."""
    lines = [
        "WARNING app.middleware api_error method=POST route=/api/v1/ledger/parse-audio status=504 error_code=EXTERNAL_API_TIMEOUT",
        "WARNING app.middleware api_error method=POST route=/api/v1/ledger/parse-audio status=503 error_code=EXTERNAL_API_TIMEOUT",
        "WARNING app.middleware api_error method=POST route=/api/v1/voice/speak status=503 error_code=EXTERNAL_API_TIMEOUT",
    ]
    assert "외부 서비스 실패(503·504) 합계 3" in render([], count_log_errors(lines))


def _api_errors(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if "api_error" in r.getMessage()]


def _token() -> str:
    return client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]


def test_api_error_line_is_logged_without_pii(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="app.middleware"):
        client.post("/api/v1/ledger/parse", json={"utterance_text": "김씨비밀상회에게 배추 외상"},
                    headers={"Authorization": f"Bearer {_token()}", "X-Idempotency-Key": str(uuid.uuid4())})
    lines = _api_errors(caplog)
    assert lines == ["api_error method=POST route=/api/v1/ledger/parse status=400 error_code=MISSING_REQUIRED_FIELD"]


def test_route_template_hides_ids_and_separates_tax_endpoints(caplog: pytest.LogCaptureFixture) -> None:
    entry_id = str(uuid.uuid4())
    h = {"Authorization": f"Bearer {_token()}", "X-Idempotency-Key": str(uuid.uuid4())}
    with caplog.at_level(logging.WARNING, logger="app.middleware"):
        client.post(f"/api/v1/ledger/{entry_id}/feedback", json={"action": "confirm"}, headers=h)
        client.post("/api/v1/tax/issue", json={"entry_id": entry_id}, headers={**h, "X-Idempotency-Key": str(uuid.uuid4())})
        client.post("/api/v1/tax/business", json={"business_number": "1"}, headers={**h, "X-Idempotency-Key": str(uuid.uuid4())})
    lines = " | ".join(_api_errors(caplog))
    assert "route=/api/v1/ledger/{entry_id}/feedback status=404" in lines
    assert "route=/api/v1/tax/issue status=404" in lines
    assert "route=/api/v1/tax/business status=400" in lines
    assert entry_id not in lines


@pytest.mark.parametrize("call, status, code", [
    (lambda: client.get("/api/v1/dashboard/unpaid"), 401, "-"),
    (lambda: client.post("/api/v1/ledger/parse", content=b"x" * 70000), 413, "PAYLOAD_TOO_LARGE"),
    (lambda: client.post("/api/v1/ledger/parse", json={"utterance_text": "x"},
                         headers={"Authorization": f"Bearer {_token()}"}), 400, "IDEMPOTENCY_KEY_REQUIRED"),
])
def test_errors_outside_idempotency_are_logged_too(caplog, call, status, code) -> None:
    """401 · 413 · missing-key 400 never reach a route handler but must still be counted."""
    with caplog.at_level(logging.WARNING, logger="app.middleware"):
        call()
    assert any(f"status={status} error_code={code}" in ln for ln in _api_errors(caplog))


def test_report_has_no_pii(db: Session, session_factory, tmp_path) -> None:
    e = _entry(db, created=datetime.now(timezone.utc))
    _feedback(db, e, "correct", {"counterparty": {"before": "김씨비밀상회", "after": "박씨비밀상회"}},
              created=datetime.now(timezone.utc))
    log = tmp_path / "railway.log"
    log.write_text("api_error method=POST route=/api/v1/ledger/parse status=400 error_code=AMOUNT_MISMATCH\n", encoding="utf-8")
    out = run(["--log-file", str(log)], session_factory)
    assert "LLM → Rule 폴백" in out and "거래처" in out and "AMOUNT_MISMATCH" in out
    assert "비밀상회" not in out and "원문" not in out and "user-b" not in out


def test_render_empty_is_dashes(db: Session) -> None:
    out = render([_today(db)], None)
    assert "| LLM → Rule 폴백 | 0 (-) |" in out


def test_unhandled_exception_is_logged_as_500(caplog: pytest.LogCaptureFixture) -> None:
    from app.main import create_app

    boom_app = create_app()

    @boom_app.get("/boom/{thing_id}")
    def boom(thing_id: str) -> None:
        raise RuntimeError("bug")

    with caplog.at_level(logging.WARNING, logger="app.middleware"):
        res = TestClient(boom_app, raise_server_exceptions=False).get("/boom/secret-id-123")
    assert res.status_code == 500
    lines = _api_errors(caplog)
    assert lines == ["api_error method=GET route=/boom/{thing_id} status=500 error_code=-"]


def test_feedback_on_manual_candidates_is_excluded_from_correction_rate(db: Session) -> None:
    """Values typed in the 400 modal are not an AI miss (수경님과 정의 ①·③)."""
    ai = _entry(db, parser="llm")
    manual = _entry(db, parser="manual")
    _feedback(db, ai, "confirm")
    _feedback(db, manual, "correct", {"counterparty": {"before": "김", "after": "김씨"}})
    m = _today(db)
    assert (m.decisions, m.manual_decisions) == (1, 1)
    assert m.correction_rate("counterparty") == 0


def test_change_vs_fill_are_counted_apart(db: Session) -> None:
    """수경님 확정안: AI 값을 바꾼 것(수정)과 AI가 비운 값을 채운 것(보완)을 나눈다."""
    e = _entry(db)
    _feedback(db, e, "correct", {
        "credit": {"before": None, "after": True},  # 외상 선택 (채움)
        "items": {"before": [{"name": "배추", "qty": "3", "unit": None, "unit_price": None, "amount": 30000}],
                  "after": [{"name": "배추", "qty": "3", "unit": "박스", "unit_price": 10000, "amount": 30000}]},
    })
    _feedback(db, e, "correct", {
        "credit": {"before": True, "after": False},  # 외상 → 현금 (바꿈)
        "total_amount": {"before": 30000, "after": 31000},
    })
    m = _today(db)
    assert m.decisions == 2
    assert m.field_fills["credit"] == 1 and m.field_changes["credit"] == 1
    assert m.correction_rate("credit") == 0.5  # only the real replacement
    assert m.item_key_fills["unit"] == 1 and m.item_key_fills["unit_price"] == 1
    assert m.item_key_changes["unit"] == 0
    assert m.correction_rate("items") == 0  # optional fields filled, nothing the AI said was replaced
    assert m.correction_rate("total_amount") == 0.5


def test_added_item_counts_as_fill(db: Session) -> None:
    e = _entry(db)
    _feedback(db, e, "correct", {
        "items": {"before": [{"name": "배추", "qty": "3"}],
                  "after": [{"name": "배추", "qty": "3"}, {"name": "무", "qty": "2"}]},
    })
    m = _today(db)
    assert m.item_key_fills["name"] == 1 and m.item_key_fills["qty"] == 1
    assert m.correction_rate("items") == 0


def test_cleared_values_count_as_correction(db: Session) -> None:
    """지움: the AI filled a value the user removed (unit_price → null, or a whole item deleted)."""
    e = _entry(db)
    _feedback(db, e, "correct", {
        "items": {"before": [{"name": "배추", "qty": "3", "unit": "박스", "unit_price": 10000, "amount": 30000},
                             {"name": "무", "qty": "1", "unit": None, "unit_price": None, "amount": None}],
                  "after": [{"name": "배추", "qty": "3", "unit": "박스", "unit_price": None, "amount": 30000}]},
    })
    _feedback(db, e, "confirm")
    m = _today(db)
    assert m.decisions == 2
    assert m.item_key_clears["unit_price"] == 1  # cleared on the kept item
    assert m.item_key_clears["name"] == 1 and m.item_key_clears["qty"] == 1  # removed item
    assert m.item_key_changes["unit_price"] == 0
    assert m.correction_rate("items") == 0.5  # AI value was wrong in 1 of 2 decisions


def test_change_and_clear_in_one_decision_count_once(db: Session) -> None:
    """PM #67: changing one item and deleting another in the same decision is one wrong decision."""
    e = _entry(db)
    _feedback(db, e, "correct", {
        "items": {"before": [{"name": "배추", "qty": "3", "amount": 30000}, {"name": "무", "qty": "1", "amount": 5000}],
                  "after": [{"name": "배추", "qty": "4", "amount": 30000}]},  # qty changed + item deleted
    })
    m = _today(db)
    assert m.decisions == 1
    assert m.field_changes["items"] == 1 and m.field_clears["items"] == 1  # both kinds still reported
    assert m.correction_rate("items") == 1.0  # not 2.0


def test_every_postprocess_rule_is_counted_in_ops_and_repair_link() -> None:
    """A new post-process rule must be added to RULE_TARGET (운영 집계) and _RULE_FIELDS (repair_of 연결),
    or it silently drops out of both (PM #90 review: #82 added 4 credit rules)."""
    import re
    from pathlib import Path

    from app.api.ledger import _RULE_FIELDS
    from app.services import postprocess
    from app.services.ops_metrics import RULE_TARGET

    source = Path(postprocess.__file__).read_text(encoding="utf-8")
    rules = set(re.findall(r'(?:applied\.append\(|_add\(applied,\s*)"([a-z_]+)"', source))
    assert rules, "rule names not found — update this test's pattern"
    assert rules <= RULE_TARGET.keys(), sorted(rules - RULE_TARGET.keys())
    assert rules <= _RULE_FIELDS.keys(), sorted(rules - _RULE_FIELDS.keys())


def test_voice_answer_success_rate_per_field(db: Session, session_factory, tmp_path) -> None:
    """PM 10/13: 칸별 성공률 for the usage test, from `voice_answer` log lines (no transcript in them)."""
    log = tmp_path / "railway.log"
    log.write_text("\n".join([
        "WARNING app.api.voice_answer voice_answer field=credit status=ok reason=-",
        "WARNING app.api.voice_answer voice_answer field=credit status=ok reason=-",
        "WARNING app.api.voice_answer voice_answer field=credit status=failed reason=ambiguous",
        "WARNING app.api.voice_answer voice_answer field=total status=failed reason=stt_failed",
        "WARNING app.api.voice_answer voice_answer field=total status=failed reason=not_in_choices",
        "WARNING app.api.voice_answer voice_answer field=total status=failed reason=not_in_choices",
        "INFO something else",
    ]), encoding="utf-8")
    counts = count_voice_answers(log.read_text(encoding="utf-8").splitlines())
    assert counts[("credit", "ok", "-")] == 2 and counts[("total", "failed", "not_in_choices")] == 2
    out = run(["--log-file", str(log)], session_factory)
    assert "| credit | 3 | 2 | 66.7% | ambiguous 1 |" in out
    assert "| total | 3 | 0 | 0.0% | not_in_choices 2 · stt_failed 1 |" in out
    assert "| qty | 0 | 0 | - | - |" in out  # every DL-31 field is listed even with no answers


def test_voice_answer_table_only_with_a_log(db: Session) -> None:
    assert "음성 되묻기 답" not in render([_today(db)], None)
