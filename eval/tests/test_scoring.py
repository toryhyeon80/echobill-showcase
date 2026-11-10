from eval.scoring import score_records
import pytest


def _confirm_row(code: str, fields: list[str]) -> dict:
    row = _record('confirm', _golden_json(), outcome='추가 확인 필요')
    row['expected_json']['credit'] = None
    row['expected_error_code'] = code
    row['required_clarifications'] = fields
    return row


def test_confirmation_checks_error_code_and_required_fields():
    """v1.3 (PM 10/5·10/6): code + required clarification fields; computed optional values are not judged."""
    row = _confirm_row('MISSING_REQUIRED_FIELD', ['credit'])
    response = {'status_code': 400, 'error_code': 'MISSING_REQUIRED_FIELD',
                'problem_fields': ['credit'], 'partial_candidate': row['expected_json']}
    prediction = {'candidate': row['expected_json'], 'validation_response': response}
    assert score_records([row], {'confirm': prediction})['confirmation_appropriateness']['correct'] == 1
    # a computed amount in the partial candidate is not an invented value any more
    computed = {**row['expected_json']['items'][0], 'amount': 9999}
    response['partial_candidate'] = {**row['expected_json'], 'items': [computed]}
    assert score_records([row], {'confirm': prediction})['confirmation_appropriateness']['correct'] == 1
    response['error_code'] = 'AMOUNT_MISMATCH'
    assert score_records([row], {'confirm': prediction})['confirmation_appropriateness']['correct'] == 0
    response['error_code'], response['problem_fields'] = 'MISSING_REQUIRED_FIELD', ['credit', 'total_amount']
    assert score_records([row], {'confirm': prediction})['confirmation_appropriateness']['correct'] == 0


@pytest.mark.parametrize('code, fields', [('MULTIPLE_COUNTERPARTIES_DETECTED', ['counterparty']),
                                          ('EXCEED_MAX_ITEMS', ['items'])])
def test_codes_without_partial_candidate_are_appropriate_when_code_and_fields_match(code, fields):
    """v1.3 (PM 10/5): MULTIPLE·EXCEED return no partial candidate; matching code and fields is appropriate."""
    row = _confirm_row(code, fields)
    response = {'status_code': 400, 'error_code': code, 'problem_fields': fields, 'partial_candidate': None}
    result = score_records([row], {'confirm': {'candidate': _candidate(), 'validation_response': response}})
    assert result['confirmation_appropriateness']['correct'] == 1


def _missing_credit_prediction(row: dict, partial: dict | None) -> dict:
    response = {'status_code': 400, 'error_code': 'MISSING_REQUIRED_FIELD', 'problem_fields': ['credit'],
                'partial_candidate': partial}
    return {'candidate': row['expected_json'], 'validation_response': response}


def _appropriate(row: dict, prediction: dict) -> int:
    return score_records([row], {'confirm': prediction})['confirmation_appropriateness']['correct']


def test_invented_total_in_partial_candidate_is_inappropriate():
    """v1.3 (PM 10/6, DL-27): code·fields match but the partial total differs from the answer → inappropriate."""
    row = _confirm_row('MISSING_REQUIRED_FIELD', ['credit'])
    assert _appropriate(row, _missing_credit_prediction(row, {**row['expected_json'], 'total_amount': 150000})) == 0


def test_only_computed_optional_fields_differing_is_appropriate():
    """v1.3 (PM 10/5): computed item amount·unit are optional fields, counted separately — still appropriate."""
    row = _confirm_row('MISSING_REQUIRED_FIELD', ['credit'])
    items = [{**item, 'amount': 7700, 'unit': '박스'} for item in row['expected_json']['items']]
    assert _appropriate(row, _missing_credit_prediction(row, {**row['expected_json'], 'items': items})) == 1


def test_item_not_in_answer_in_partial_candidate_is_inappropriate():
    """v1.3 (PM 10/6): an item the answer does not have is an invented required value."""
    row = _confirm_row('MISSING_REQUIRED_FIELD', ['credit'])
    extra = {'name': '양파', 'qty': 5, 'unit': None, 'unit_price': None, 'amount': None}
    partial = {**row['expected_json'], 'items': [*row['expected_json']['items'], extra]}
    assert _appropriate(row, _missing_credit_prediction(row, partial)) == 0


def test_wrong_item_quantity_in_partial_candidate_is_inappropriate():
    row = _confirm_row('MISSING_REQUIRED_FIELD', ['credit'])
    items = [{**row['expected_json']['items'][0], 'qty': 30}, row['expected_json']['items'][1]]
    assert _appropriate(row, _missing_credit_prediction(row, {**row['expected_json'], 'items': items})) == 0


def test_normal_record_answered_with_400_is_a_wrong_transaction():
    """v1.3 (PM 10/6, 기획서 §8.3): required fields all right + 400 → wrong; 200 → right; no response → right."""
    row = _record('normal', _golden_json())
    ok = {'status_code': 200, 'error_code': None, 'problem_fields': [], 'partial_candidate': None}
    bad = {'status_code': 400, 'error_code': 'AMOUNT_MISMATCH', 'problem_fields': ['items[0].unit_price'],
           'partial_candidate': None}
    for response, correct in ((bad, 0), (ok, 1), (None, 1)):
        prediction = {'candidate': _golden_json()}
        if response is not None:
            prediction['validation_response'] = response
        result = score_records([row], {'normal': prediction})
        assert result['transaction_exact']['correct'] == correct
        assert result['records'][0]['transaction_exact'] is bool(correct)


def test_report_only_counts_credit_confirmed_by_200_and_normal_400():
    confirm = _confirm_row('MISSING_REQUIRED_FIELD', ['credit'])
    normal = _record('normal', _golden_json())
    ok = {'status_code': 200, 'error_code': None, 'problem_fields': [], 'partial_candidate': None}
    bad = {'status_code': 400, 'error_code': 'MISSING_REQUIRED_FIELD', 'problem_fields': ['credit'],
           'partial_candidate': None}
    result = score_records([confirm, normal], {
        'confirm': {'candidate': {**confirm['expected_json'], 'credit': False}, 'validation_response': ok},
        'normal': {'candidate': _golden_json(), 'validation_response': bad},
    })
    assert result['credit_wrongly_confirmed'] == {'count': 1, 'ids': ['confirm']}
    assert result['normal_rejected_400'] == {'count': 1, 'evaluated': 1, 'ids': ['normal']}



_OK = {'status_code': 200, 'error_code': None, 'problem_fields': [], 'partial_candidate': None}


def _flip(record: dict, saved_credit, response: dict | None = _OK) -> dict:
    prediction = {'candidate': {**record['expected_json'], 'credit': saved_credit}}
    if response is not None:
        prediction['validation_response'] = response
    return score_records([record], {record['id']: prediction})['credit_flip_saved']


@pytest.mark.parametrize('truth, saved, direction', [(False, True, 'cash_to_credit'), (True, False, 'credit_to_cash')])
def test_credit_flip_saved_counts_a_normal_200_with_opposite_credit_by_direction(truth, saved, direction):
    """PM 10/8 (report only): 정상 건 200 저장인데 credit이 정답과 반대 — 방향별로 센다."""
    row = _record('flip', {**_golden_json(), 'credit': truth})
    other = 'credit_to_cash' if direction == 'cash_to_credit' else 'cash_to_credit'
    assert _flip(row, saved) == {direction: 1, other: 0, 'ids': {direction: ['flip'], other: []}}
    assert _flip(row, truth)[direction] == 0


def test_credit_flip_saved_skips_400_null_credit_and_confirmation_rows():
    """되묻기(400)·credit null 저장·추가 확인 행은 세지 않는다; 기존 외상 잘못 확정과 별개."""
    row = _record('flip', {**_golden_json(), 'credit': False})
    bad = {'status_code': 400, 'error_code': 'MISSING_REQUIRED_FIELD', 'problem_fields': ['credit'],
           'partial_candidate': None}
    empty = {'cash_to_credit': 0, 'credit_to_cash': 0, 'ids': {'cash_to_credit': [], 'credit_to_cash': []}}
    assert _flip(row, True, bad) == empty
    assert _flip(row, None) == empty
    confirm = _confirm_row('MISSING_REQUIRED_FIELD', ['credit'])
    result = score_records([confirm, row], {'confirm': {'candidate': {**confirm['expected_json'], 'credit': True},
                                                        'validation_response': _OK},
                                            'flip': {'candidate': row['expected_json'], 'validation_response': _OK}})
    assert result['credit_flip_saved'] == empty
    assert result['credit_wrongly_confirmed'] == {'count': 1, 'ids': ['confirm']}


def test_credit_flip_saved_is_not_available_without_a_validation_response():
    row = _record('flip', {**_golden_json(), 'credit': False})
    assert _flip(row, True, None) == {'status': 'not_available'}


def test_duplicate_names_match_quantity_before_order():
    truth = _golden_json()
    truth['items'][1]['name'] = '무'
    pred = {**truth, 'items': list(reversed(truth['items']))}
    result = score_records([_record('same', truth)], {'same': {'candidate': pred}})
    assert result['transaction_exact']['correct'] == 1
    assert result['optional_field_errors']['total_fp'] == 0


def test_optional_missing_and_format_errors_are_separate():
    truth = _golden_json()
    pred = _golden_json()
    pred['items'][0]['unit'] = None
    pred['credit'] = 1
    result = score_records([_record('types', truth)], {'types': {'candidate': pred}})
    assert result['optional_field_missing']['by_field']['unit'] == 1
    assert result['format_errors'] == [{'id': 'types', 'path': 'credit'}]


def test_numeric_string_quantity_is_not_a_true_positive():
    truth = _golden_json()
    pred = _golden_json()
    pred['items'][0]['qty'] = '3'
    result = score_records([_record('string', truth)], {'string': {'candidate': pred}})
    assert result['transaction_exact']['correct'] == 0
    assert result['format_errors'] == [{'id': 'string', 'path': 'items[0].qty'}]


def test_credit_integer_is_not_boolean_and_field_accuracy_is_reported():
    expected = _golden_json()
    predicted = _golden_json()
    predicted['credit'] = 1
    result = score_records([_record('typed', expected)], {'typed': {'candidate': predicted}})
    assert result['transaction_exact']['correct'] == 0
    assert result['field_accuracy']['credit'] == {'correct': 0, 'total': 1, 'rate': 0.0}


def test_duplicate_golden_ids_are_rejected():
    row = _record('duplicate', _golden_json())
    with pytest.raises(ValueError, match='duplicate'):
        score_records([row, row], {})


def test_error_with_partial_candidate_cannot_pass():
    row = _record('error', _golden_json())
    result = score_records([row], {'error': {'candidate': _golden_json(), 'error': 'timeout'}})
    assert result['transaction_exact']['correct'] == 0
    assert result['generation']['failed'] == 1


def _record(
    record_id: str,
    expected_json: dict,
    *,
    outcome: str = "정상 구조화 가능",
    total_source: str = "stated",
) -> dict:
    return {
        "id": record_id,
        "expected_json": expected_json,
        "expected_outcome": outcome,
        "confirm_type": None if outcome == "정상 구조화 가능" else "필수값 누락",
        "required_clarifications": [],
        "difficulty_label": "명확한 표현",
        "review_status": "검수 완료",
        "notes": {"total_source": total_source},
    }


def _candidate(counterparty: str = "가 나 상회") -> dict:
    return {
        "counterparty": counterparty,
        "items": [
            {"name": "배 추", "qty": 2.0, "unit": "망", "unit_price": 5000, "amount": 10000},
            {"name": "무", "qty": 3, "unit": "개", "unit_price": 1000, "amount": 3000},
        ],
        "total_amount": 13000,
        "credit": True,
    }


def _golden_json() -> dict:
    return {
        "counterparty": "가나상회",
        "items": [
            {"name": "무", "qty": 3, "unit": "개", "unit_price": 1000, "amount": 3000},
            {"name": "배추", "qty": 2, "unit": "망", "unit_price": 5000, "amount": 10000},
        ],
        "total_amount": 13000,
        "credit": True,
    }


def test_scores_reordered_items_and_space_only_name_differences_as_exact():
    records = [_record("one", _golden_json())]
    predictions = {"one": {"candidate": _candidate(), "error": None}}

    result = score_records(records, predictions)

    assert result["transaction_exact"] == {"correct": 1, "total": 1, "rate": 1.0}
    assert result["required_fields_micro"] == {
        "tp": 7,
        "fp": 0,
        "fn": 0,
        "precision": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    assert result["space_normalization"] == {"field_matches": 2, "transaction_matches": 1}
    assert result["records"] == [
        {
            "id": "one",
            "generation_success": True,
            "transaction_exact": True,
            "amount_exact": True,
            "confirmation_appropriate": None,
            "optional_field_fp": 0,
        }
    ]


def test_unmatched_wrong_item_name_counts_item_name_and_qty_as_fp_and_fn():
    expected = _golden_json()
    expected["items"] = [expected["items"][0]]
    predicted = _candidate("가나상회")
    predicted["items"] = [
        {"name": "당근", "qty": 3, "unit": "개", "unit_price": 1000, "amount": 3000}
    ]
    records = [_record("wrong-name", expected)]

    result = score_records(
        records,
        {"wrong-name": {"candidate": predicted, "error": None}},
    )

    assert result["required_fields_micro"]["tp"] == 3
    assert result["required_fields_micro"]["fp"] == 2
    assert result["required_fields_micro"]["fn"] == 2
    assert result["required_fields_micro"]["f1"] == 0.6
    assert result["transaction_exact"]["correct"] == 0


def test_failed_generation_stays_in_denominators_and_counts_required_values_as_fn():
    expected = _golden_json()
    expected["items"] = [expected["items"][0]]
    records = [_record("failed", expected, total_source="derived")]

    result = score_records(records, {"failed": {"candidate": None, "error": "timeout"}})

    assert result["generation"] == {"succeeded": 0, "failed": 1, "total": 1}
    assert result["transaction_exact"] == {"correct": 0, "total": 1, "rate": 0.0}
    assert result["amount_exact"]["overall"] == {"correct": 0, "total": 1, "rate": 0.0}
    assert result["amount_exact"]["derived"] == {"correct": 0, "total": 1, "rate": 0.0}
    assert result["required_fields_micro"]["fn"] == 5


def test_optional_errors_count_each_non_null_wrong_or_invented_item_field():
    expected = _golden_json()
    expected["items"] = [expected["items"][0]]
    expected["items"][0].update({"unit": None, "unit_price": 1000, "amount": 3000})
    predicted = _candidate("가나상회")
    predicted["items"] = [
        {"name": "무", "qty": 3, "unit": "봉", "unit_price": None, "amount": 4000}
    ]

    result = score_records(
        [_record("optional", expected)],
        {"optional": {"candidate": predicted, "error": None}},
    )

    assert result["optional_field_errors"] == {
        "total_fp": 2,
        "by_field": {"unit": 1, "unit_price": 0, "amount": 1},
    }


def test_additional_confirmation_is_reported_unavailable_without_validation_response():
    expected = _golden_json()
    records = [_record("confirm", expected, outcome="추가 확인 필요")]

    result = score_records(
        records,
        {"confirm": {"candidate": _candidate("가나상회"), "error": None}},
    )

    assert result["confirmation_appropriateness"] == {
        "status": "not_available",
        "total": 1,
        "reason": "validation response is not implemented",
    }
    assert result["transaction_exact"]["total"] == 0
    assert result["required_fields_micro"]["f1"] is None


@pytest.mark.parametrize('candidate, path', [
    ({'items': '사과'}, 'items'),
    ({'items': ['사과']}, 'items[0]'),
    ('not a dict', '$'),
])
def test_malformed_candidate_is_generation_failure_not_crash(candidate, path):
    result = score_records([_record('bad', _golden_json())], {'bad': {'candidate': candidate}})
    assert result['generation']['failed'] == 1
    assert result['transaction_exact']['correct'] == 0
    assert result['format_errors'] == [{'id': 'bad', 'path': path}]


@pytest.mark.parametrize('fields', [['items[0].qty', 'items[0].unit_price', 'total_amount'],
                                    ['items[0].amount', 'total_amount']])
def test_amount_mismatch_accepts_qty_price_or_item_amount_side(fields):
    """v1.4 (PM 10/8, g-dev-037): {qty, unit_price, total} and {item amount, total} are both appropriate."""
    row = _confirm_row('AMOUNT_MISMATCH', ['items[0].qty', 'items[0].unit_price', 'total_amount'])
    response = {'status_code': 400, 'error_code': 'AMOUNT_MISMATCH', 'problem_fields': fields,
                'partial_candidate': None}
    assert _appropriate(row, {'candidate': _candidate(), 'validation_response': response}) == 1


@pytest.mark.parametrize('fields', [['items[0].amount'], ['items[0].unit_price', 'total_amount'],
                                    ['items[0].qty', 'items[0].unit_price', 'items[0].amount', 'total_amount']])
def test_amount_mismatch_other_field_sets_stay_inappropriate(fields):
    """v1.4: only the two PM sides count; a partial or mixed field set is still inappropriate."""
    row = _confirm_row('AMOUNT_MISMATCH', ['items[0].qty', 'items[0].unit_price', 'total_amount'])
    response = {'status_code': 400, 'error_code': 'AMOUNT_MISMATCH', 'problem_fields': fields,
                'partial_candidate': None}
    assert _appropriate(row, {'candidate': _candidate(), 'validation_response': response}) == 0


def test_item_amount_swap_applies_only_to_amount_mismatch():
    """v1.4: other error codes keep exact set equality."""
    row = _confirm_row('MISSING_REQUIRED_FIELD', ['items[0].qty'])
    response = {'status_code': 400, 'error_code': 'MISSING_REQUIRED_FIELD', 'problem_fields': ['items[0].amount'],
                'partial_candidate': None}
    assert _appropriate(row, {'candidate': _candidate(), 'validation_response': response}) == 0
