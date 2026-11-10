"""Contract tests — API schema ↔ Golden ↔ decision log DL-01·DL-03.

If someone renames a field in the API schema or the Golden data (e.g. `qty` → `quantity`,
`total_amount` → `total_amount_krw`), these tests fail instead of the drift going unnoticed.
"""

import json
import re
import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.config import Settings
from app.main import app
from app.schemas.ledger import CandidateFields, ParseCandidateResponse
from app.services.validation import validate_candidate

DATA_DIR = Path(__file__).resolve().parents[2] / "data"
GOLDEN_DEV = DATA_DIR / "golden" / "golden_dev.jsonl"
SEED_FILES = sorted((DATA_DIR / "seed").glob("*.jsonl"))

# DL-01 — the agreed public names. Change only together with the decision log.
DL01_CANDIDATE_KEYS = {"counterparty", "items", "total_amount", "credit"}
DL01_ITEM_KEYS = {"name", "qty", "unit", "unit_price", "amount"}
DL01_RESPONSE_KEYS = DL01_CANDIDATE_KEYS | {
    "entry_id",
    "utterance_text",
    "status",
    "tax_status",
    "parser_type",
    "is_fallback_used",
    "processing_time_ms",
}

# DL-03 problem_fields path: top-level field, `items`, or `items[<n>].<item field>`.
_PATH = re.compile(
    r"^(counterparty|total_amount|credit|items)$|^items\[\d+\]\.(" + "|".join(DL01_ITEM_KEYS) + r")$"
)


def _records(path: Path) -> list[dict]:
    with path.open(encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


GOLDEN = _records(GOLDEN_DEV)
# Seed uses the same answer format, so the same contract applies (data/seed/build.py)
LABELED = GOLDEN + [rec for path in SEED_FILES for rec in _records(path)]


def test_schema_field_names_match_dl01() -> None:
    """Pydantic schema exposes exactly the DL-01 names."""
    assert set(CandidateFields.model_fields) == DL01_CANDIDATE_KEYS
    assert set(ParseCandidateResponse.model_fields) == DL01_RESPONSE_KEYS
    item_model = CandidateFields.model_fields["items"].annotation.__args__[0]
    assert set(item_model.model_fields) == DL01_ITEM_KEYS


def test_parse_response_keys_match_dl01() -> None:
    """Live /parse response uses DL-01 keys only."""
    client = TestClient(app)
    token = client.post("/api/v1/auth/dev-token", json={"user_id": "dev-user-1"}).json()[
        "access_token"
    ]
    body = client.post(
        "/api/v1/ledger/parse",
        json={"utterance_text": "김씨에게 배추 3박스 외상 30000원"},
        headers={"Authorization": f"Bearer {token}", "X-Idempotency-Key": str(uuid.uuid4())},
    ).json()
    assert set(body) == DL01_RESPONSE_KEYS
    for item in body["items"]:
        assert set(item) == DL01_ITEM_KEYS


def test_golden_file_present() -> None:
    """Golden dev set exists and is non-empty."""
    assert GOLDEN, f"no records in {GOLDEN_DEV}"


def _loc_to_path(loc: tuple) -> str:
    """Pydantic error loc ('items', 0, 'amount') → DL-03 path 'items[0].amount'."""
    path = ""
    for part in loc:
        path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else part)
    return path


@pytest.mark.parametrize("rec", LABELED, ids=[r["id"] for r in LABELED])
def test_golden_expected_json_matches_schema(rec: dict) -> None:
    """Every Golden/Seed answer validates against the API candidate schema (extra keys forbidden).

    AMOUNT_OUT_OF_RANGE answers keep the out-of-range value on purpose (API answers 400),
    so they may fail only the range check, and only on their `required_clarifications`.
    """
    if rec.get("expected_error_code") != "AMOUNT_OUT_OF_RANGE":
        CandidateFields.model_validate(rec["expected_json"])
        return
    try:
        CandidateFields.model_validate(rec["expected_json"])
    except ValidationError as exc:
        for err in exc.errors():
            path = _loc_to_path(err["loc"])
            assert err["type"] == "greater_than", f"{rec['id']}: {path} {err['type']}"
            assert path in rec["required_clarifications"], f"{rec['id']}: {path} not in clarifications"


# Item and counterparty counts come from the parser, not the answer JSON (G4: items=[])
_PARSER_COUNTED = {"EXCEED_MAX_ITEMS", "MULTIPLE_COUNTERPARTIES_DETECTED"}
VALIDATED = [r for r in LABELED if r["expected_error_code"] not in _PARSER_COUNTED]


@pytest.mark.parametrize("rec", VALIDATED, ids=[r["id"] for r in VALIDATED])
def test_answer_matches_server_validation(rec: dict) -> None:
    """Server validation on the answer gives the labeled error code and problem fields (DL-03).

    Keeps the answers and validation.py from drifting apart: a 200 answer passes, a 400
    answer fails with the same `expected_error_code` and `required_clarifications`.
    """
    settings = Settings(_env_file=None, app_env="development")
    failure = validate_candidate(rec["expected_json"], settings=settings)
    got = (failure.error_code, sorted(failure.problem_fields)) if failure else (None, [])
    assert got == (rec["expected_error_code"], sorted(rec["required_clarifications"])), rec["id"]


@pytest.mark.parametrize("rec", LABELED, ids=[r["id"] for r in LABELED])
def test_golden_clarification_paths_follow_dl03(rec: dict) -> None:
    """`required_clarifications` uses DL-03 problem_fields path notation."""
    for path in rec["required_clarifications"]:
        assert _PATH.match(path), f"{rec['id']}: '{path}' is not a DL-03 path"
