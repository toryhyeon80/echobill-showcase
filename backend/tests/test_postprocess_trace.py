"""PM #23 — post-process version + applied rules are kept with the candidate and counted in ops metrics."""

import logging
import uuid
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from app.main import app
from app.models import FeedbackEvent, LedgerEntry
from app.services.ops_metrics import compute, count_postprocess_400, default_periods
from app.services.parse_mock import ParserResult

client = TestClient(app)
FIELDS = {
    "counterparty": "김씨",
    "items": [{"name": "배추", "qty": 3, "unit": "박스", "unit_price": None, "amount": 30000}],
    "total_amount": 30000,
    "credit": True,
}


def _post(monkeypatch: pytest.MonkeyPatch, fields: dict, rules: list[str], version: str | None = "pp-1.3"):
    monkeypatch.setattr(
        "app.api.ledger.structure_utterance",
        lambda text, *a, **k: ParserResult(
            fields=dict(fields), utterance_text=text, parser_type="llm",
            postprocess_version=version, postprocess_applied=list(rules),
        ),
    )
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    return client.post(
        "/api/v1/ledger/parse",
        json={"utterance_text": "김씨비밀상회 배추 3박스 3만원"},
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())},
    )


def test_trace_is_stored_with_the_candidate(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    res = _post(monkeypatch, FIELDS, ["credit_from_text"])
    assert res.status_code == 200
    entry = db.query(LedgerEntry).one()
    assert entry.postprocess_version == "pp-1.3"
    assert entry.postprocess_applied == ["credit_from_text"]
    assert "postprocess" not in res.text  # internal only, not in the API candidate


def test_ran_with_no_rule_fired_is_an_empty_list(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    _post(monkeypatch, FIELDS, [])
    entry = db.query(LedgerEntry).one()
    assert (entry.postprocess_version, entry.postprocess_applied) == ("pp-1.3", [])


def test_rule_and_manual_paths_store_null(db: Session) -> None:
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "user-b"}).json()["access_token"]
    h = lambda: {"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())}  # noqa: E731
    client.post("/api/v1/ledger/parse", json={"utterance_text": "김씨에게 배추 3박스 외상 30000원"}, headers=h())
    client.post("/api/v1/ledger/parse", json={"utterance_text": "김씨 배추", "candidate": FIELDS}, headers=h())
    assert [(e.postprocess_version, e.postprocess_applied) for e in db.query(LedgerEntry)] == [(None, None)] * 2


def test_rule_that_ends_in_400_leaves_a_countable_log_line(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, db: Session
) -> None:
    """credit is required: a cleared credit is a 400 that stores nothing — the log line keeps the count."""
    with caplog.at_level(logging.WARNING, logger="app.api.ledger"):
        res = _post(monkeypatch, {**FIELDS, "credit": None}, ["credit_cleared_no_word"])
    assert res.status_code == 400
    assert db.query(LedgerEntry).count() == 0
    lines = [r.getMessage() for r in caplog.records if "postprocess_400" in r.getMessage()]
    assert lines == ["postprocess_400 version=pp-1.3 rules=credit_cleared_no_word error_code=MISSING_REQUIRED_FIELD"]
    assert "김씨" not in caplog.text
    assert count_postprocess_400(lines)[("credit_cleared_no_word", "MISSING_REQUIRED_FIELD")] == 1


def _entry(db: Session, rules: list[str] | None, credit: bool | None = True) -> LedgerEntry:
    e = LedgerEntry(entry_id=str(uuid.uuid4()), user_id="u", status="candidate", counterparty="김씨", items=[],
                    total_amount=1000, credit=credit, parser_type="llm", postprocess_version="pp-1.3",
                    postprocess_applied=rules, created_at=datetime.now(timezone.utc))
    db.add(e)
    db.commit()
    return e


def _decide(db: Session, e: LedgerEntry, diff: dict | None) -> None:
    db.add(FeedbackEvent(user_id="u", ledger_entry_id=e.id, action="correct" if diff else "confirm",
                         corrected_data=diff, created_at=datetime.now(timezone.utc)))
    db.commit()


def test_rule_stats_applied_decided_corrected(db: Session) -> None:
    kept = _entry(db, ["credit_from_text"])
    flipped = _entry(db, ["credit_from_text"])
    priced = _entry(db, ["unit_price_to_amount", "unit_price_cleared"])
    _entry(db, ["credit_from_text"])  # applied, not decided yet
    _decide(db, kept, None)
    _decide(db, flipped, {"credit": {"before": True, "after": False}})
    _decide(db, priced, {"items": {"before": [{"name": "배추", "amount": 30000, "unit_price": None}],
                                   "after": [{"name": "배추", "amount": 30000, "unit_price": 10000}]}})
    label, start, end = default_periods()[0]
    m = compute(db, label, start, end)
    assert (m.rule_applied["credit_from_text"], m.rule_decided["credit_from_text"],
            m.rule_corrected["credit_from_text"]) == (3, 2, 1)
    assert m.rule_credit_after[("credit_from_text", False)] == 1
    assert m.rule_corrected["unit_price_to_amount"] == 1 and m.rule_corrected["unit_price_cleared"] == 1


def test_pipeline_carries_trace_from_llm_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """The bug PM found: parse_pipeline dropped postprocess_version/applied from the LLM result."""
    from app.config import Settings
    from app.services import parse_pipeline
    from app.services.parse_llm import LedgerExtraction, LLMParseResult

    llm = LLMParseResult(
        candidate=LedgerExtraction.model_validate({**FIELDS, "items": [{**FIELDS["items"][0], "qty": 3.0}]}),
        model="gpt-4o-mini", processing_time_ms=900,
        postprocess_version="pp-1.3", postprocess_applied=["unit_price_cleared"],
    )
    monkeypatch.setattr(parse_pipeline.parse_llm, "parse_utterance_with_llm", lambda *a, **k: llm)
    result = parse_pipeline.structure_utterance(
        "김씨에게 배추 3박스 외상 30000원", Settings(_env_file=None, app_env="development", llm_parse_enabled=True)
    )
    assert (result.postprocess_version, result.postprocess_applied) == ("pp-1.3", ["unit_price_cleared"])


# --- repair_of (PM #23): rule → 400 → user's repair submit, linked ----------------------------

def _token(user_id: str = "user-b") -> str:
    return client.post("/api/v1/auth/dev-token", json={"user_id": user_id}).json()["access_token"]


def _cleared_400(monkeypatch: pytest.MonkeyPatch, key: str, user_id: str = "user-b"):
    """LLM said nothing about payment → rule cleared credit → 400 (stores nothing)."""
    monkeypatch.setattr(
        "app.api.ledger.structure_utterance",
        lambda text, *a, **k: ParserResult(fields={**FIELDS, "credit": None}, utterance_text=text,
                                           parser_type="llm", postprocess_version="pp-1.3",
                                           postprocess_applied=["credit_cleared_no_word"]),
    )
    return client.post("/api/v1/ledger/parse", json={"utterance_text": "김씨 배추 3박스 3만원"},
                       headers={"Authorization": f"Bearer {_token(user_id)}", "X-Idempotency-Key": key})


def _repair(credit: bool, repair_of: str | None, user_id: str = "user-b"):
    body = {"utterance_text": "김씨 배추 3박스 3만원", "candidate": {**FIELDS, "credit": credit}}
    if repair_of is not None:
        body["repair_of"] = repair_of
    return client.post("/api/v1/ledger/parse", json=body,
                       headers={"Authorization": f"Bearer {_token(user_id)}", "X-Idempotency-Key": str(uuid.uuid4())})


def test_400_trace_is_kept_on_the_idempotency_record(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    from app.models import IdempotencyRecord

    key = str(uuid.uuid4())
    assert _cleared_400(monkeypatch, key).status_code == 400
    record = db.query(IdempotencyRecord).filter_by(key=key).one()
    assert record.postprocess_trace == {"version": "pp-1.3", "rules": ["credit_cleared_no_word"]}
    assert "김씨" not in str(record.postprocess_trace)  # rule names only


def test_repair_links_rules_to_the_users_choice(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    key = str(uuid.uuid4())
    _cleared_400(monkeypatch, key)
    res = _repair(True, key)
    assert res.status_code == 200 and res.json()["parser_type"] == "manual"
    entry = db.query(LedgerEntry).one()
    assert (entry.parser_type, entry.credit) == ("manual", True)
    assert (entry.postprocess_version, entry.postprocess_applied) == ("pp-1.3", ["credit_cleared_no_word"])


@pytest.mark.parametrize("case", ["other_user", "unknown", "expired", "none"])
def test_unlinkable_repair_still_saves_without_link(case: str, monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """Not found / another user's / expired → saved normally, just not linked (nothing revealed)."""
    from datetime import timedelta

    from app.models import IdempotencyRecord

    key = str(uuid.uuid4())
    _cleared_400(monkeypatch, key, user_id="user-a")
    if case == "expired":
        rec = db.query(IdempotencyRecord).filter_by(key=key).one()
        rec.user_id = "user-b"
        rec.created_at = datetime.now(timezone.utc) - timedelta(hours=25)
        db.commit()
    repair_of = {"other_user": key, "unknown": str(uuid.uuid4()), "expired": key, "none": None}[case]
    res = _repair(True, repair_of, user_id="user-b")
    assert res.status_code == 200
    entry = db.query(LedgerEntry).one()
    assert (entry.postprocess_version, entry.postprocess_applied) == (None, None)


def test_metrics_report_what_users_chose_after_the_rule(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    for credit in (True, True, False):
        key = str(uuid.uuid4())
        _cleared_400(monkeypatch, key)
        _repair(credit, key)
    label, start, end = default_periods()[0]
    m = compute(db, label, start, end)
    assert m.rule_repaired["credit_cleared_no_word"] == 3
    assert m.rule_repair_credit[("credit_cleared_no_word", True)] == 2  # 미수금 — the rule saved these
    assert m.rule_repair_credit[("credit_cleared_no_word", False)] == 1
    assert m.rule_applied["credit_cleared_no_word"] == 0  # manual repairs are not "applied" candidates


def test_voice_400_trace_links_too(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """DL-31 voice answers end in the same repair submit — the /parse-audio 400 must keep its trace."""
    from app.models import IdempotencyRecord

    monkeypatch.setattr("app.api.ledger.transcribe", lambda *a, **k: "김씨 배추 3박스 3만원")
    monkeypatch.setattr(
        "app.api.ledger.structure_utterance",
        lambda text, *a, **k: ParserResult(fields={**FIELDS, "credit": None}, utterance_text=text, parser_type="llm",
                                           postprocess_version="pp-1.3", postprocess_applied=["credit_cleared_no_word"]),
    )
    key = str(uuid.uuid4())
    res = client.post("/api/v1/ledger/parse-audio", files={"file": ("a.webm", b"x" * 500, "audio/webm")},
                      headers={"Authorization": f"Bearer {_token()}", "X-Idempotency-Key": key})
    assert res.status_code == 400 and res.json()["utterance_text"]  # DL-29 still there
    assert db.query(IdempotencyRecord).filter_by(key=key).one().postprocess_trace["rules"] == ["credit_cleared_no_word"]
    assert _repair(False, key).status_code == 200
    assert db.query(LedgerEntry).one().postprocess_applied == ["credit_cleared_no_word"]


def test_unrelated_rule_is_not_blamed_for_the_400(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """unit_price_cleared ran, but the 400 is about credit → only the credit rule is recorded."""
    from app.models import IdempotencyRecord

    monkeypatch.setattr(
        "app.api.ledger.structure_utterance",
        lambda text, *a, **k: ParserResult(fields={**FIELDS, "credit": None}, utterance_text=text, parser_type="llm",
                                           postprocess_version="pp-1.3",
                                           postprocess_applied=["credit_cleared_no_word", "unit_price_cleared"]),
    )
    key = str(uuid.uuid4())
    client.post("/api/v1/ledger/parse", json={"utterance_text": "x"},
                headers={"Authorization": f"Bearer {_token()}", "X-Idempotency-Key": key})
    assert db.query(IdempotencyRecord).filter_by(key=key).one().postprocess_trace["rules"] == ["credit_cleared_no_word"]


def test_one_400_links_to_one_repair_only(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    key = str(uuid.uuid4())
    _cleared_400(monkeypatch, key)
    _repair(True, key)
    _repair(True, key)  # resubmitted (e.g. draft kept repair_of)
    linked = [e for e in db.query(LedgerEntry) if e.postprocess_applied]
    assert len(linked) == 1


def test_failed_repair_keeps_the_link_and_logs_no_second_400(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, db: Session
) -> None:
    key = str(uuid.uuid4())
    _cleared_400(monkeypatch, key)
    bad = {"utterance_text": "x", "candidate": {**FIELDS, "credit": True, "total_amount": 99999}, "repair_of": key}
    with caplog.at_level(logging.WARNING, logger="app.api.ledger"):
        res = client.post("/api/v1/ledger/parse", json=bad,
                          headers={"Authorization": f"Bearer {_token()}", "X-Idempotency-Key": str(uuid.uuid4())})
    assert res.status_code == 400  # user's own values are inconsistent
    assert caplog.text.count("postprocess_400") == 1  # only the original — the repair's 400 is the user's
    assert _repair(True, key).status_code == 200
    assert db.query(LedgerEntry).one().postprocess_applied == ["credit_cleared_no_word"]  # still linkable


def test_hash_unchanged_for_requests_without_repair_of() -> None:
    """Retries across the deploy (same key, same body) must replay, not 409."""
    from app.schemas.ledger import ParseRequest

    body = ParseRequest(utterance_text="김씨 배추")
    assert body.model_dump(mode="json", exclude={"repair_of"}) == {"utterance_text": "김씨 배추", "candidate": None}



def test_entry_and_link_consumption_are_one_commit(
    monkeypatch: pytest.MonkeyPatch, db: Session, session_factory
) -> None:
    """PM #71: if saving fails, the link must stay usable — no half-done state that a retry duplicates."""
    from app.models import IdempotencyRecord
    from app.schemas.ledger import CandidateFields
    from app.services.ledger_store import create_candidate

    key = str(uuid.uuid4())
    _cleared_400(monkeypatch, key)

    def failing_commit(self):
        raise RuntimeError("db down")

    with session_factory() as s:
        monkeypatch.setattr(Session, "commit", failing_commit)
        with pytest.raises(RuntimeError):
            create_candidate(
                s, "user-b", CandidateFields.model_validate({**FIELDS, "credit": True}),
                ParserResult(fields={}, utterance_text="x", parser_type="manual",
                             postprocess_version="pp-1.3", postprocess_applied=["credit_cleared_no_word"]),
                consume_repair_key=key,
            )
        monkeypatch.undo()
    db.expire_all()
    assert db.query(LedgerEntry).count() == 0  # entry not saved…
    assert db.query(IdempotencyRecord).filter_by(key=key).one().postprocess_trace is not None  # …link kept
    monkeypatch.setattr("app.api.ledger.structure_utterance", lambda *a, **k: None)  # repair path never parses
    assert _repair(True, key).status_code == 200  # the retry links and saves exactly once
    assert db.query(LedgerEntry).count() == 1
    assert db.query(LedgerEntry).one().postprocess_applied == ["credit_cleared_no_word"]


def test_same_key_with_different_repair_of_is_409(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """repair_of is part of the request: the same key with a changed repair_of is a different body."""
    key_a, key_b, repair_key = str(uuid.uuid4()), str(uuid.uuid4()), str(uuid.uuid4())
    _cleared_400(monkeypatch, key_a)
    _cleared_400(monkeypatch, key_b)
    body = {"utterance_text": "김씨 배추 3박스 3만원", "candidate": {**FIELDS, "credit": True}}
    h = {"Authorization": f"Bearer {_token()}", "X-Idempotency-Key": repair_key}
    assert client.post("/api/v1/ledger/parse", json={**body, "repair_of": key_a}, headers=h).status_code == 200
    res = client.post("/api/v1/ledger/parse", json={**body, "repair_of": key_b}, headers=h)
    assert res.status_code == 409
    assert res.json()["error_code"] == "IDEMPOTENCY_KEY_REUSED"
    assert db.query(LedgerEntry).count() == 1


def test_total_rule_counts_a_corrected_total_only(db: Session) -> None:
    """PM 10/12: total_restored_stated is "corrected" when the user changed total_amount, not other fields."""
    kept = _entry(db, ["total_restored_stated"])
    changed = _entry(db, ["total_restored_stated"])
    other = _entry(db, ["total_restored_stated"])
    _decide(db, kept, None)
    _decide(db, changed, {"total_amount": {"before": 9600, "after": 12600}})
    _decide(db, other, {"counterparty": {"before": "김씨", "after": "김씨네"}})
    label, start, end = default_periods()[0]
    m = compute(db, label, start, end)
    assert (m.rule_decided["total_restored_stated"], m.rule_corrected["total_restored_stated"]) == (3, 1)


def test_total_rule_is_linked_to_its_amount_mismatch_400(monkeypatch: pytest.MonkeyPatch, db: Session) -> None:
    """The restored total ends in AMOUNT_MISMATCH (problem field total_amount) → the rule is kept for repair_of."""
    from app.models import IdempotencyRecord

    fields = {**FIELDS, "items": [{"name": "배추", "qty": 3, "unit": "박스", "unit_price": 10000, "amount": 30000}],
              "total_amount": 25000}
    monkeypatch.setattr(
        "app.api.ledger.structure_utterance",
        lambda text, *a, **k: ParserResult(fields=dict(fields), utterance_text=text, parser_type="llm",
                                           postprocess_version="pp-1.4",
                                           postprocess_applied=["total_restored_stated", "credit_from_text"]),
    )
    key = str(uuid.uuid4())
    res = client.post("/api/v1/ledger/parse", json={"utterance_text": "x"},
                      headers={"Authorization": f"Bearer {_token()}", "X-Idempotency-Key": key})
    assert res.status_code == 400 and res.json()["error_code"] == "AMOUNT_MISMATCH"
    assert db.query(IdempotencyRecord).filter_by(key=key).one().postprocess_trace["rules"] == ["total_restored_stated"]
