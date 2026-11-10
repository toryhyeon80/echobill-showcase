"""Deterministic scoring for EchoBill ledger extraction predictions."""

from __future__ import annotations

import re
from collections import Counter, defaultdict, deque
from decimal import Decimal, InvalidOperation
from typing import Any


SCORING_VERSION = "v1.4"
NORMAL_OUTCOME = "정상 구조화 가능"
OPTIONAL_FIELDS = ("unit", "unit_price", "amount")


def _without_spaces(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return "".join(value.split())


def _numeric_equal(left: Any, right: Any) -> bool:
    if left is None or right is None or isinstance(left, bool) or isinstance(right, bool):
        return left is right
    if not isinstance(left, (int, float, Decimal)) or not isinstance(right, (int, float, Decimal)):
        return False
    try:
        return Decimal(str(left)) == Decimal(str(right))
    except (InvalidOperation, ValueError):
        return False


def _field_equal(field: str, expected: Any, predicted: Any) -> bool:
    if field == 'credit':
        return type(expected) is type(predicted) and expected == predicted
    if field in {'total_amount', 'unit_price', 'amount'}:
        return type(expected) is type(predicted) and expected == predicted
    if field in {"counterparty", "name"}:
        return _without_spaces(expected) == _without_spaces(predicted)
    if field == "qty":
        return _numeric_equal(expected, predicted)
    return expected == predicted


def _shape_error(candidate: Any) -> str | None:
    """Return the path of a structural defect that makes a candidate unscorable."""
    if not isinstance(candidate, dict):
        return "$"
    items = candidate.get("items")
    if items is not None and not isinstance(items, list):
        return "items"
    for index, item in enumerate(items or []):
        if not isinstance(item, dict):
            return f"items[{index}]"
    return None


def _ratio(correct: int, total: int) -> dict[str, Any]:
    return {
        "correct": correct,
        "total": total,
        "rate": round(correct / total, 6) if total else None,
    }


def _match_items(
    expected_items: list[dict[str, Any]],
    predicted_items: list[dict[str, Any]],
) -> tuple[list[tuple[dict[str, Any], dict[str, Any]]], list[dict[str, Any]], list[dict[str, Any]]]:
    predicted_by_name: defaultdict[Any, deque[tuple[int, dict[str, Any]]]] = defaultdict(deque)
    for index, item in enumerate(predicted_items):
        predicted_by_name[_without_spaces(item.get("name"))].append((index, item))

    matched: list[tuple[dict[str, Any], dict[str, Any]]] = []
    unmatched_expected: list[dict[str, Any]] = []
    used_indices: set[int] = set()
    deferred = []
    for expected in expected_items:
        candidates = predicted_by_name[_without_spaces(expected.get('name'))]
        exact = next((pair for pair in candidates if _numeric_equal(expected.get('qty'), pair[1].get('qty'))), None)
        if exact is not None:
            candidates.remove(exact)
            used_indices.add(exact[0])
            matched.append((expected, exact[1]))
        else:
            deferred.append(expected)
    for expected in deferred:
        candidates = predicted_by_name[_without_spaces(expected.get("name"))]
        if candidates:
            index, predicted = candidates.popleft()
            used_indices.add(index)
            matched.append((expected, predicted))
        else:
            unmatched_expected.append(expected)

    unmatched_predicted = [
        item for index, item in enumerate(predicted_items) if index not in used_indices
    ]
    return matched, unmatched_expected, unmatched_predicted


def _clarification_fields_match(error_code: Any, predicted: Any, required: Any) -> bool:
    """Return True when the 400 problem fields equal the required clarifications as a set.

    v1.4 (PM 10/8, DL-27): for AMOUNT_MISMATCH an item's {qty, unit_price} and {amount} are the same
    clarification, so {qty, unit_price, total_amount} and {amount, total_amount} are both appropriate.
    """
    predicted_set, required_set = set(predicted or []), set(required or [])
    if error_code != 'AMOUNT_MISMATCH':
        return predicted_set == required_set
    return _canonical_mismatch_fields(predicted_set) == _canonical_mismatch_fields(required_set)


def _canonical_mismatch_fields(fields: set[str]) -> set[str]:
    """Collapse each item's {qty, unit_price} or {amount} group into one token; other fields stay as is."""
    groups: dict[str, set[str]] = defaultdict(set)
    canonical: set[str] = set()
    for field in fields:
        match = re.fullmatch(r'(items\[\d+\])\.(qty|unit_price|amount)', field)
        if match:
            groups[match.group(1)].add(match.group(2))
        else:
            canonical.add(field)
    for item, parts in groups.items():
        if parts in ({'qty', 'unit_price'}, {'amount'}):
            canonical.add(f'{item}.value')
        else:
            canonical.update(f'{item}.{part}' for part in parts)
    return canonical


def _no_invented_required_values(expected: dict[str, Any], partial_candidate: Any) -> bool:
    """Return False when a 400 partial candidate carries a required value that differs from the answer.

    v1.3 (PM 10/6, DL-27): invented required values (counterparty·total_amount·credit, item name·qty, an item
    not in the answer) are inappropriate. Optional fields (unit·unit_price·amount) are counted separately.
    No partial candidate (MULTIPLE·EXCEED) means nothing was invented.
    """
    if not isinstance(partial_candidate, dict):
        return True
    for field in ('counterparty', 'total_amount', 'credit'):
        value = partial_candidate.get(field)
        if value is not None and not _field_equal(field, expected.get(field), value):
            return False
    partial_items = [item for item in partial_candidate.get('items') or [] if isinstance(item, dict)]
    matched, _, extra = _match_items(expected.get('items') or [], partial_items)
    if extra:
        return False
    for expected_item, partial_item in matched:
        for field in ('name', 'qty'):
            value = partial_item.get(field)
            if value is not None and not _field_equal(field, expected_item.get(field), value):
                return False
    return True


def _add_f1_value(counts: Counter[str], expected: Any, predicted: Any, *, equal: bool) -> None:
    if expected is None and predicted is None:
        return
    if expected is not None and predicted is not None and equal:
        counts["tp"] += 1
    elif expected is None:
        counts["fp"] += 1
    elif predicted is None:
        counts["fn"] += 1
    else:
        counts["fp"] += 1
        counts["fn"] += 1


def _f1_result(counts: Counter[str]) -> dict[str, Any]:
    tp, fp, fn = counts["tp"], counts["fp"], counts["fn"]
    precision_denominator = tp + fp
    recall_denominator = tp + fn
    f1_denominator = 2 * tp + fp + fn
    return {
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "precision": round(tp / precision_denominator, 6) if precision_denominator else None,
        "recall": round(tp / recall_denominator, 6) if recall_denominator else None,
        "f1": round(2 * tp / f1_denominator, 6) if f1_denominator else None,
    }


def score_records(
    golden_records: list[dict[str, Any]],
    predictions: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Score predictions using the D03 deterministic comparison contract."""
    ids = [record['id'] for record in golden_records]
    if len(ids) != len(set(ids)):
        raise ValueError('duplicate Golden IDs')
    generation = Counter()
    transaction_correct = 0
    transaction_total = 0
    required_counts: Counter[str] = Counter()
    amount_counts: defaultdict[str, Counter[str]] = defaultdict(Counter)
    optional_errors: Counter[str] = Counter()
    optional_missing: Counter[str] = Counter()
    format_errors = []
    space_field_matches = 0
    space_transactions: set[str] = set()
    confirmation_total = 0
    confirmation_evaluated = 0
    confirmation_correct = 0
    # Report-only (not scoring rules): credit confirmed by a 200 where the answer asks for it, needless 400s
    credit_confirmed_ids: list[str] = []
    normal_rejected_ids: list[str] = []
    normal_evaluated = 0
    # Report-only (PM 10/8): a normal record saved by a 200 with credit opposite to the answer, by direction
    credit_flip_ids: dict[str, list[str]] = {'cash_to_credit': [], 'credit_to_cash': []}
    outcome_counts: Counter[str] = Counter()
    difficulty_counts: Counter[str] = Counter()
    record_results: list[dict[str, Any]] = []
    field_counts = defaultdict(Counter)

    for record in golden_records:
        record_id = record["id"]
        outcome = record.get("expected_outcome")
        outcome_counts[outcome] += 1
        difficulty_counts[record.get("difficulty_label", "unknown")] += 1
        prediction = predictions.get(record_id) or {}
        candidate = prediction.get("candidate")
        if prediction.get('error'):
            candidate = None
        if candidate is not None:
            shape_error = _shape_error(candidate)
            if shape_error is not None:
                # A structurally broken candidate is a generation failure, not a crash.
                format_errors.append({'id': record_id, 'path': shape_error})
                candidate = None
        generation_success = candidate is not None
        if candidate is None:
            generation["failed"] += 1
            candidate = {}
        else:
            generation["succeeded"] += 1

        expected = record["expected_json"]
        predicted_items = candidate.get("items") or []
        typed_fields = [(key, candidate.get(key)) for key in ('counterparty', 'total_amount', 'credit')]
        for index, item in enumerate(predicted_items):
            typed_fields.extend((f'items[{index}].{key}', value) for key, value in item.items())
        for path, value in typed_fields:
            field = path.split('.')[-1]
            valid = (value is None or
                     (field in {'name', 'counterparty', 'unit'} and isinstance(value, str)) or
                     (field == 'credit' and type(value) is bool) or
                     (field == 'qty' and type(value) in (int, float) and Decimal(str(value)).is_finite()) or
                     (field in {'amount', 'unit_price', 'total_amount'} and type(value) is int))
            if not valid:
                format_errors.append({'id': record_id, 'path': path})
        expected_items = expected.get("items") or []
        matched, unmatched_expected, unmatched_predicted = _match_items(
            expected_items, predicted_items
        )
        comparisons = [(field, expected.get(field), candidate.get(field))
                       for field in ('counterparty', 'total_amount', 'credit')]
        for left, right in matched + [(item, {}) for item in unmatched_expected]:
            comparisons.extend((field, left.get(field), right.get(field))
                               for field in ('name', 'qty', *OPTIONAL_FIELDS))
        for field, truth, value in comparisons:
            if truth is not None:
                field_counts[field]['total'] += 1
                field_counts[field]['correct'] += int(_field_equal(field, truth, value))

        record_transaction_exact: bool | None = None
        # Per-ID view of 추가 확인 적합성 (eval/regression.py); None unless a confirmation record has a response
        record_confirmation_appropriate: bool | None = None
        if outcome == NORMAL_OUTCOME:
            transaction_total += 1
            transaction_matches = True

            for field in ("counterparty", "total_amount", "credit"):
                expected_value = expected.get(field)
                predicted_value = candidate.get(field)
                equal = _field_equal(field, expected_value, predicted_value)
                _add_f1_value(required_counts, expected_value, predicted_value, equal=equal)
                transaction_matches = transaction_matches and equal

            transaction_matches = transaction_matches and not unmatched_expected and not unmatched_predicted
            for expected_item, predicted_item in matched:
                for field in ("name", "qty"):
                    expected_value = expected_item.get(field)
                    predicted_value = predicted_item.get(field)
                    equal = _field_equal(field, expected_value, predicted_value)
                    _add_f1_value(required_counts, expected_value, predicted_value, equal=equal)
                    transaction_matches = transaction_matches and equal

            for expected_item in unmatched_expected:
                for field in ("name", "qty"):
                    _add_f1_value(
                        required_counts,
                        expected_item.get(field),
                        None,
                        equal=False,
                    )
            for predicted_item in unmatched_predicted:
                for field in ("name", "qty"):
                    _add_f1_value(
                        required_counts,
                        None,
                        predicted_item.get(field),
                        equal=False,
                    )
            response = prediction.get('validation_response')
            if isinstance(response, dict):
                normal_evaluated += 1
                if response.get('status_code') == 400:
                    normal_rejected_ids.append(record_id)
                    # v1.3 (PM 10/6, DL-27, 기획서 §8.3): a normal sentence answered with 400 is a wrong transaction.
                    transaction_matches = False
                saved_credit, expected_credit = candidate.get('credit'), expected.get('credit')
                if (response.get('status_code') == 200 and type(saved_credit) is bool
                        and type(expected_credit) is bool and saved_credit != expected_credit):
                    credit_flip_ids['cash_to_credit' if saved_credit else 'credit_to_cash'].append(record_id)
            if transaction_matches:
                transaction_correct += 1
            record_transaction_exact = transaction_matches
        else:
            confirmation_total += 1
            response = prediction.get('validation_response')
            if isinstance(response, dict):
                confirmation_evaluated += 1
                # v1.3 (PM 10/5·10/6, DL-27): error code + required clarification fields + no invented required
                # value in partial_candidate. Computed optional fields are counted separately, not judged here.
                record_confirmation_appropriate = bool(
                    response.get('status_code') == 400
                    and record.get('expected_error_code') is not None
                    and response.get('error_code') == record.get('expected_error_code')
                    and _clarification_fields_match(response.get('error_code'), response.get('problem_fields'),
                                                    record.get('required_clarifications'))
                    and _no_invented_required_values(expected, response.get('partial_candidate')))
                confirmation_correct += int(record_confirmation_appropriate)
                if ('credit' in (record.get('required_clarifications') or []) and response.get('status_code') == 200
                        and candidate.get('credit') is not None):
                    credit_confirmed_ids.append(record_id)

        expected_total = expected.get("total_amount")
        record_amount_exact: bool | None = None
        if expected_total is not None:
            total_source = record.get("notes", {}).get("total_source", "unknown")
            amount_counts["overall"]["total"] += 1
            amount_counts[total_source]["total"] += 1
            record_amount_exact = _field_equal('total_amount', expected_total, candidate.get('total_amount'))
            if record_amount_exact:
                amount_counts["overall"]["correct"] += 1
                amount_counts[total_source]["correct"] += 1

        transaction_has_space_match = False
        name_pairs = [(expected.get("counterparty"), candidate.get("counterparty"))]
        name_pairs.extend((left.get("name"), right.get("name")) for left, right in matched)
        for expected_name, predicted_name in name_pairs:
            if (
                isinstance(expected_name, str)
                and isinstance(predicted_name, str)
                and expected_name != predicted_name
                and _without_spaces(expected_name) == _without_spaces(predicted_name)
            ):
                space_field_matches += 1
                transaction_has_space_match = True
        if transaction_has_space_match:
            space_transactions.add(record_id)

        optional_before = sum(optional_errors.values())
        for expected_item, predicted_item in matched + [(item, {}) for item in unmatched_expected]:
            for field in OPTIONAL_FIELDS:
                if expected_item.get(field) is not None and predicted_item.get(field) is None:
                    optional_missing[field] += 1
        for expected_item, predicted_item in matched:
            for field in OPTIONAL_FIELDS:
                predicted_value = predicted_item.get(field)
                if predicted_value is not None and not _field_equal(field, expected_item.get(field), predicted_value):
                    optional_errors[field] += 1
        for predicted_item in unmatched_predicted:
            for field in OPTIONAL_FIELDS:
                if predicted_item.get(field) is not None:
                    optional_errors[field] += 1
        record_results.append(
            {
                "id": record_id,
                "generation_success": generation_success,
                "transaction_exact": record_transaction_exact,
                "amount_exact": record_amount_exact,
                "confirmation_appropriate": record_confirmation_appropriate,
                "optional_field_fp": sum(optional_errors.values()) - optional_before,
            }
        )

    generation_total = len(golden_records)
    amount_result = {
        source: _ratio(counts["correct"], counts["total"])
        for source, counts in amount_counts.items()
    }
    for source in ("overall", "stated", "derived"):
        amount_result.setdefault(source, _ratio(0, 0))

    return {
        "dataset": {
            "total": generation_total,
            "by_outcome": dict(outcome_counts),
            "by_difficulty": dict(difficulty_counts),
        },
        "generation": {
            "succeeded": generation["succeeded"],
            "failed": generation_total - generation["succeeded"],
            "total": generation_total,
        },
        "transaction_exact": _ratio(transaction_correct, transaction_total),
        "amount_exact": amount_result,
        "required_fields_micro": _f1_result(required_counts),
        "optional_field_errors": {
            "total_fp": sum(optional_errors.values()),
            "by_field": {field: optional_errors[field] for field in OPTIONAL_FIELDS},
        },
        "space_normalization": {
            "field_matches": space_field_matches,
            "transaction_matches": len(space_transactions),
        },
        "confirmation_appropriateness": ({
            "status": "not_available",
            "total": confirmation_total,
            "reason": "validation response is not implemented",
        } if not confirmation_evaluated else {
            'status': 'complete' if confirmation_evaluated == confirmation_total else 'partial',
            **_ratio(confirmation_correct, confirmation_total),
            'evaluated': confirmation_evaluated,
            'unavailable': confirmation_total - confirmation_evaluated,
        }),
        "credit_wrongly_confirmed": ({"status": "not_available"} if not confirmation_evaluated else
                                     {"count": len(credit_confirmed_ids), "ids": credit_confirmed_ids}),
        "normal_rejected_400": ({"status": "not_available"} if not normal_evaluated else
                                {"count": len(normal_rejected_ids), "evaluated": normal_evaluated,
                                 "ids": normal_rejected_ids}),
        "credit_flip_saved": ({"status": "not_available"} if not normal_evaluated else
                              {**{direction: len(ids) for direction, ids in credit_flip_ids.items()},
                               "ids": credit_flip_ids}),
        "records": record_results,
        "format_errors": format_errors,
        "optional_field_missing": {'total': sum(optional_missing.values()),
                                   'by_field': {field: optional_missing[field] for field in OPTIONAL_FIELDS}},
        "field_accuracy": {field: _ratio(counts['correct'], counts['total'])
                           for field, counts in field_counts.items()},
    }
