from dataclasses import dataclass

from eval.score_voice_answer import DEFAULT_SET, FIELD_ORDER, judge_row, load_set, score


@dataclass(frozen=True)
class _Result:
    status: str
    value: object = None
    unit: str | None = None
    reason: str | None = None


def _row(field: str, status: str, value=None, unit=None) -> dict:
    return {"id": "x", "field": field, "text": "t", "expected_status": status, "expected_value": value,
            "expected_unit": unit}


def test_judge_row_counts_fill_on_should_fail_as_wrong():
    """A should-fail answer that comes back ok is a wrong fill, even with a plausible value."""
    assert judge_row(_row("total_amount", "failed"), "ok", 10000, None) == "wrong_fill"
    assert judge_row(_row("total_amount", "failed"), "failed", None, None) == "correct_fail"


def test_judge_row_compares_meaning_not_type():
    """qty "3" == 3, unit 키로 == kg, missing unit is not a conflict, but a wrong unit is."""
    assert judge_row(_row("quantity", "ok", 3, "단"), "ok", "3", "단") == "hit"
    assert judge_row(_row("quantity", "ok", 10, "kg"), "ok", "10", "키로") == "hit"
    assert judge_row(_row("quantity", "ok", 3, "단"), "ok", "3", None) == "hit"
    assert judge_row(_row("quantity", "ok", 3, "단"), "ok", "3", "박스") == "wrong_fill"
    assert judge_row(_row("credit", "ok", True), "ok", 1, None) == "wrong_fill"
    assert judge_row(_row("counterparty", "ok", "약수슈퍼"), "ok", "약수슈퍼야", None) == "wrong_fill"
    assert judge_row(_row("credit", "ok", True), "failed", None, None) == "miss"


def test_score_passes_choices_and_item_names_and_applies_gate():
    """mismatch_choice → total + choices; item_names only when asked; gate = 90% + 0 wrong fills."""
    calls = []

    def fake(field, text, *, choices=None, item_names=None):
        calls.append((field, choices, item_names))
        return _Result("ok", 30000) if text == "삼만" else _Result("failed", reason="ambiguous")

    rows = [{**_row("mismatch_choice", "ok", 30000), "text": "삼만", "choices": [30000, 36000]},
            {**_row("counterparty", "failed"), "text": "배추요", "item_names": ["배추"]}]
    result = score(rows, fake, pass_item_names=False)

    assert calls == [("total", [30000, 36000], None), ("counterparty", None, None)]
    assert result["fields"]["mismatch_choice"]["enable_1014"] is True
    assert result["fields"]["counterparty"]["reasons_on_should_fail"] == {"ambiguous": 1}
    score(rows, fake, pass_item_names=True)
    assert calls[-1] == ("counterparty", None, ["배추"])


def test_judge_set_shape():
    """20 rows per field (15 answerable + 5 should-fail; credit 14 + 6 after PM 10/8 moved "나중에 받기로" to fail),
    plus PM 10/16 trailing "?" rows (quantity 3 + 4, total 2 + 4, mismatch 1 + 0); ids unique; all rows dev;
    counterparty rows carry item_names."""
    rows = load_set(DEFAULT_SET)
    assert len({r["id"] for r in rows}) == len(rows)
    assert all(r["split"] == "dev" for r in rows)
    for field in FIELD_ORDER:
        rows_f = [r for r in rows if r["field"] == field]
        answerable = sum(r["expected_status"] == "ok" for r in rows_f)
        extra_ok, extra_fail = {"quantity": (3, 4), "total_amount": (2, 4), "mismatch_choice": (1, 0)}.get(field, (0, 0))
        assert len(rows_f) == 20 + extra_ok + extra_fail
        assert answerable == (14 if field == "credit" else 15) + extra_ok
    assert all(r["item_names"] for r in rows if r["field"] == "counterparty")
    assert all(len(r["choices"]) == 2 for r in rows if r["field"] == "mismatch_choice")
