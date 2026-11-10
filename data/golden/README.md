# Golden Set (에코빌)

> **SSOT:** 기획서 §8.1·§8.3 ([원문](../../docs/source/기획서.md)) · 결정 로그 DL-01·DL-02·DL-03 ([ALIGN §7](../../docs/DOC_CRITERIA_ALIGNMENT.md#7-결정-로그-decision-log))  
> **합계 목표(가안):** Dev+Test **50~100** (세부 분할은 [D03 결정안](../D03_D04_결정안.md)). 「최종만 100」아님.  
> **출제·검수:** PM · **채점 스크립트:** AI(김수경) · 거래 전체 정확도 / 금액 Exact / 필수 필드 F1 / 추가 확인 적합성

## 파일

| 파일 | 용도 |
|---|---|
| `data.py` | **정답 원천** (사람이 수정하는 유일한 파일) |
| `build.py` | `data.py` → jsonl·검수용 md 생성 + 자동 검산 |
| `golden_dev.jsonl` | 채점 스크립트용 (한 줄=1건, UTF-8) — **생성물, 직접 수정 금지** |
| `golden_dev_review.md` | PM 검수용 카드 — **생성물, 직접 수정 금지** |
| `추가20건_검토안.md` | 신규 20건 재작성 목적·ID별 검수 포인트·비교 실행 조건 |
| `golden_test.jsonl` | 최종 평가용 (튜닝·Champion 판단 **금지**) — 아직 미작성 |

정답을 고칠 때:

```bash
cd data/golden
# data.py 수정 후
python3 build.py   # 검산 실패 시 exit 1, 생성물 미갱신
```

## 레코드 스키마 (v2.3)

기획서 §8.1 레코드 구조를 따르고, `expected_json` 안쪽 키는 API 명세와 같다 (DL-01·DL-02).  
**계약 테스트:** `backend/tests/test_contract_golden.py`가 모든 `expected_json`을 API 후보 스키마로 검증하고 `required_clarifications` 경로 표기(DL-03)를 검사한다. 키 이름이 어긋나면 `pytest`가 실패한다.

| 필드 | 값 | 설명 |
|---|---|---|
| `id` | `g-dev-001` | |
| `utterance_text` | String | 가명 처리한 발화 (STT 가정 텍스트) |
| `expected_json` | Object | 아래 참고. 추가 확인 사례는 확인 가능한 값만 채우고 나머지 `null` |
| `difficulty_label` | `명확한 표현` \| `복잡·생략 표현` \| `null` | **4조건에서 자동 산출** (D03-4). 4조건 중 하나라도 미판정이면 `null`. 기대 결과 라벨과 독립 |
| `difficulty_conditions` | `{word_order, no_correction, required_direct, standard_unit_int}` | 각 `true` / `false` / `null`(미판정). `data.py`의 `COND`에서 옴 |
| `difficulty_label_draft` | `명확한 표현` \| `복잡·생략 표현` | v2.1까지의 초안 라벨. 참고용, 채점에 쓰지 않음 |
| `expected_outcome` | `정상 구조화 가능` \| `추가 확인 필요` | 두 트랙 채점 기준 |
| `confirm_type` | `필수값 누락` \| `산술 모순` \| `입력 범위 위반` \| `금액 범위 위반` \| `null` | 추가 확인 사유 분류 (채점 그룹) |
| `expected_error_code` | `MISSING_REQUIRED_FIELD` 등 \| `null` | API가 돌려줘야 할 오류 코드 (DL-03). `build.py`가 산출. 입력 범위 위반은 보완 필드 `["counterparty"]` / `["items"]`로 구분 |
| `required_clarifications` | String[] | 보완 필요 필드. DL-03 `problem_fields`와 같은 경로 표기 |
| `reject_reason` | String \| `null` | 입력 범위 위반 시 안내 사유 |
| `review_status` | `미검수` \| `검수 완료` \| `재검토` | **검수 완료만 Golden에 편입** (기획서 §8.1). `data.py`의 `REVIEW`에서 옴 |
| `reviewed_at` | `YYYY-MM-DD` \| `null` | PM 검수일 |
| `review_memo` | String | PM 검수 메모 (재검토 사유 등) |
| `split` | `개발용 Golden` \| `최종 Golden` | |
| `source` | `합성·직접 작성` \| `피드백` | |
| `group_id` | `G01`… | 파생 문장 그룹. 세트 간 누출 방지 단위 |
| `version` | `v2.3` | |
| `notes` | `{total_source, memo}` | `total_source`: `stated`(발화 명시) \| `derived`(계산 도출) |
| `change_log` | String[] | 정답 변경 이력 |

### `expected_json`

```json
{
  "counterparty": "대박마트",
  "items": [
    {"name": "계란", "qty": 2, "unit": "판", "unit_price": null, "amount": 45000}
  ],
  "total_amount": 45000,
  "credit": true
}
```

- 필수 5종: `counterparty` · `items[].name` · `items[].qty` · `total_amount` · `credit`
- 선택: `unit` · `unit_price` · `amount` — 발화에 없으면 `null`, 추정 금지
- 거래처 1곳 · 품목 ≤3 · 수량 임의 1 금지

### `required_clarifications` 표기 (DL-03)

- 최상위 필드: `counterparty`, `total_amount`, `credit`
- 품목 필드: `items[0].qty`처럼 인덱스 포함
- 품목 자체가 없으면: `items`

## 배치 현황

| 배치 | 파일 | 건수 | 상태 |
|---|---|---|---|
| dev v2.3 기존 | `golden_dev.jsonl` | 40 (정상 28 · 추가 확인 12 / 명확 15 · 복잡 25) | **PM 검수 완료 (2026-09-28)** |
| dev v2.5 신규 | `golden_dev.jsonl` | 20 (정상 14 · 추가 확인 6 / 명확 14 · 복잡 6) | **PM 검수 완료 (2026-09-29)** (`g-dev-041`~`060`) |
| — | `golden_test.jsonl` | 0 | Dev 안정 후 격리 출제 |

## PM 검수 방법

1. `golden_dev_review.md`를 카드 단위로 읽는다.
2. 판정한 건을 `data.py` 맨 아래 `REVIEW`와 **`COND`(난이도 4조건)** 에 한 줄씩 적는다. 검수 완료로 적는 건은 4조건도 반드시 함께 판정한다.

   ```python
   REVIEW = {
       "g-dev-001": ("검수 완료", "2026-09-25", ""),
       "g-dev-005": ("재검토", "2026-09-25", "거래처명 '한식당' vs '한식집' 확인"),
   }
   COND = {  # (① 어순, ② 정정 없음, ③ 필수 5종 직접 등장, ④ 표준 단위·정수 수량)
       "g-dev-001": (True, True, False, True),   # 수량 미언급 → ③ False → 복잡·생략 표현
   }
   ```

   4조건이 모두 `True`면 `명확한 표현`, 하나라도 `False`면 `복잡·생략 표현`으로 **자동 산출**된다. 카드에 초안 라벨과 다르면 ⚠️가 표시된다. 「표준 단위」 열거 목록은 아직 없다 (D03 후속) — 그 전까지는 판단 근거를 `REVIEW` 메모에 남긴다.

3. `python3 build.py` — 검수 현황이 md 상단과 콘솔에 표시된다. 없는 ID·잘못된 상태·검수일 누락은 검산 실패로 막힌다.
4. `재검토` 건은 `ROWS`의 정답을 고친 뒤 다시 판정한다. **검수 완료 후 정답을 고치면 해당 `REVIEW` 줄을 지우거나 `재검토`로 바꾼다.**

`REVIEW`에 없는 건은 자동으로 `미검수`, v1.0 대비 변경 이력(`change_log`)이 있으면 `재검토`로 표시된다. md의 체크박스는 표시용이라 직접 체크해도 `build.py` 실행 시 사라진다.

### 판정 기준

- [ ] 금액·외상·거래처·품목명이 현장 표현과 맞는지
- [ ] `정상 구조화 가능` 건: 품목 금액 합계 ↔ 총액 일치 (금액이 모두 있는 경우)
- [ ] `추가 확인 필요` 건: 보완 필요 필드가 실제 누락·모순과 일치하는지
- [ ] Test 작성 시 Dev와 같은 `group_id`·문장 파생 누출 없는지
