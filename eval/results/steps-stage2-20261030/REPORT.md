# 측정 틀 2단계 — 단계별 기여도·시간 (저장된 출력 재생)

- 만든 시각: 2026-10-30T12:52:54+09:00 · 코드 `e33baa5bbbebdc110dad1dcf248e4db72e07e51d` (dirty=False)
- 채점 v1.4 · 정규화기 ko-num-1.5 · 로컬 단계 반복 30회 · Windows 11 · Intel64 Family 6 Model 189 Stepping 1, GenuineIntel · Python 3.12.14
- API·AI 호출 없음 (저장된 답만 재생, 0원). 값은 회차별 `r1 · r2 · r3`.
- 지점: (A) AI 원 출력 · (B) A+후처리 · (C) B+서버 검증. A·B는 검증이 없으니 생성된 후보를 모두 저장으로 본다.
- 정상 문장 틀린 값 저장 = 정상 문장인데 거래가 틀린 채 200 (#205 run_cascade와 같은 정의) · 추가 확인 문장 묻지 않고 저장 = 되물어야 할 문장이 200.
- 후처리·검증은 지금 코드로 다시 돌린다 (저장된 답에 실행 당시 validation_response가 있어도 쓰지 않는다).

## 데이터 출처

- 채점 v1.4 · 후처리 pp-1.5 · 정규화기 ko-num-1.5 (모두 지금 main `e33baa5` 코드)
- 후처리 pp-1.5(main e33baa5) 기준. #241(pp-1.6) 머지 후 동결 커밋으로 다시 재생 예정.
- **dev** 정답 `data/golden/golden_dev.jsonl` (sha256 `570110e27ff09a765b09570f39c1bc62980196fcfa40e7f5020f35bacad4ba50`)
  - 예측 r1 `handoff/runs/dev60_30s/gpt-4o-mini-20261021-120731-kst-mini30-r1.predictions.jsonl` · 2026-10-21T12:08:54+09:00 · gpt-4o-mini · 프롬프트 `configs/prompts/parse_llm_system_p3.txt`
  - 예측 r2 `handoff/runs/dev60_30s/gpt-4o-mini-20261021-120731-kst-mini30-r2.predictions.jsonl` · 2026-10-21T12:15:51+09:00 · gpt-4o-mini · 프롬프트 `configs/prompts/parse_llm_system_p3.txt`
  - 예측 r3 `handoff/runs/dev60_30s/gpt-4o-mini-20261021-120731-kst-mini30-r3.predictions.jsonl` · 2026-10-21T12:23:34+09:00 · gpt-4o-mini · 프롬프트 `configs/prompts/parse_llm_system_p3.txt`
- **seed2** 정답 `echobill-seed2/data/seed/seed_v2_improve.jsonl` (sha256 `45305219894d55ff3798f007c312bdbd5b9357f464166180ca702ec7147746a8`)
  - 예측 r1 `handoff/runs/seed2_haiku_20261023/gpt-4o-mini-20261023-153539-kst-mini30-r1.predictions.jsonl` · 2026-10-23T15:39:22+09:00 · gpt-4o-mini · 프롬프트 `configs/prompts/parse_llm_system_p3.txt`
  - 예측 r2 `handoff/runs/seed2_haiku_20261023/gpt-4o-mini-20261023-153539-kst-mini30-r2.predictions.jsonl` · 2026-10-23T15:46:45+09:00 · gpt-4o-mini · 프롬프트 `configs/prompts/parse_llm_system_p3.txt`
  - 예측 r3 `handoff/runs/seed2_haiku_20261023/gpt-4o-mini-20261023-153539-kst-mini30-r3.predictions.jsonl` · 2026-10-23T15:54:14+09:00 · gpt-4o-mini · 프롬프트 `configs/prompts/parse_llm_system_p3.txt`

## dev (개발용 Golden, 60건)

- AI 출력: gpt-4o-mini · 프롬프트 `configs/prompts/parse_llm_system_p3.txt` · 측정 2026-10-21T12:08 · 2026-10-21T12:15 · 2026-10-21T12:23
- 후처리 버전(지금 코드): pp-1.5

### 단계별 기여도

| 지표 | (A) AI 원 출력 | (B) +후처리 | (C) +검증 |
|---|---|---|---|
| 거래 전체 정확도 | 39/42 · 40/42 · 38/42 | 39/42 · 40/42 · 38/42 | 39/42 · 40/42 · 38/42 |
| 금액 Exact | 54/56 · 53/56 · 53/56 | 54/56 · 54/56 · 53/56 | 54/56 · 54/56 · 53/56 |
| 필수 F1 | 98.57% · 98.78% · 97.53% | 98.57% · 98.78% · 97.53% | 98.57% · 98.78% · 97.53% |
| 정상 문장 틀린 값 저장(200) | 3 · 2 · 3 | 3 · 2 · 3 | 1 · 1 · 1 |
| 추가 확인 문장 묻지 않고 저장(200) | 18 · 18 · 18 | 18 · 18 · 18 | 1 · 1 · 1 |
| 되묻기(400) | 0 · 0 · 0 | 0 · 0 · 0 | 19 · 18 · 19 |
| 외상 잘못 확정 | 3 · 3 · 3 | 0 · 0 · 0 | 0 · 0 · 0 |

### 후처리 규칙별 (applied 라벨, A→B 거래 판정)

| 규칙 | 적용 | 틀림→맞음 | 맞음→틀림 | 변화 없음 | 추가 확인 문장(거래 판정 없음) | 금액 틀림→맞음 | 금액 맞음→틀림 |
|---|---|---|---|---|---|---|---|
| `credit_cleared_no_word` | 3 · 3 · 3 | 0 · 0 · 0 | 0 · 0 · 0 | 0 · 0 · 0 | 3 · 3 · 3 | 0 · 1 · 0 | 0 · 0 · 0 |
| `credit_from_text` | 1 · 1 · 1 | 0 · 0 · 0 | 0 · 0 · 0 | 0 · 0 · 0 | 1 · 1 · 1 | 0 · 0 · 0 | 0 · 0 · 0 |
| `item_amount_invented` | 1 · 3 · 1 | 0 · 0 · 0 | 0 · 0 · 0 | 0 · 0 · 0 | 1 · 3 · 1 | 0 · 1 · 0 | 1 · 1 · 1 |
| `pack_spec_qty_cleared` | 1 · 1 · 1 | 0 · 0 · 0 | 0 · 0 · 0 | 0 · 0 · 0 | 1 · 1 · 1 | 0 · 0 · 0 | 0 · 0 · 0 |
| `total_restored_spoken` | 1 · 2 · 1 | 0 · 0 · 0 | 0 · 0 · 0 | 0 · 0 · 0 | 1 · 2 · 1 | 1 · 2 · 1 | 0 · 0 · 0 |
| `unit_price_cleared` | 28 · 28 · 27 | 0 · 0 · 0 | 0 · 0 · 0 | 18 · 18 · 17 | 10 · 10 · 10 | 1 · 2 · 1 | 1 · 1 · 1 |
| `unit_price_to_amount` | 1 · 1 · 1 | 0 · 0 · 0 | 0 · 0 · 0 | 0 · 0 · 0 | 1 · 1 · 1 | 1 · 1 · 1 | 0 · 0 · 0 |

- 한 문장에 규칙이 여럿 붙으면 각 규칙에 같은 변화를 센다.
- 거래 틀림→맞음 ID: 없음 · 맞음→틀림 ID: 없음
- 금액 틀림→맞음 ID: g-dev-004, g-dev-007 · 맞음→틀림 ID: g-dev-056

### 따로 볼 문장 (원인)

- **g-dev-056** (추가 확인 필요, 되물을 칸: items) — r1: 후처리로 금액 맞음→틀림 · r2: 후처리로 금액 맞음→틀림 · r3: 후처리로 금액 맞음→틀림
  - r1: AI 외상=True · 거래 종류=sale · 규칙 unit_price_cleared, item_amount_invented · 바뀐 값 total_amount: 32000 → None; items[0].unit_price: 4000 → None; items[1].unit_price: 6000 → None; items[1].amount: 18000 → None; items[2].unit_price: 8000 → None; items[3].unit_price: 12000 → None · 검증 400 EXCEED_MAX_ITEMS ['items']
  - r2: AI 외상=True · 거래 종류=sale · 규칙 unit_price_cleared, item_amount_invented · 바뀐 값 total_amount: 32000 → None; items[0].unit_price: 4000 → None; items[1].unit_price: 6000 → None; items[1].amount: 18000 → None; items[2].unit_price: 8000 → None; items[3].unit_price: 12000 → None · 검증 400 EXCEED_MAX_ITEMS ['items']
  - r3: AI 외상=True · 거래 종류=sale · 규칙 unit_price_cleared, item_amount_invented · 바뀐 값 total_amount: 32000 → None; items[0].unit_price: 4000 → None; items[1].unit_price: 6000 → None; items[1].amount: 18000 → None; items[2].unit_price: 8000 → None; items[3].unit_price: 12000 → None · 검증 400 EXCEED_MAX_ITEMS ['items']
  - 원인: 품목이 상한보다 많아 되물어야 하는 문장이다. AI가 품목마다 단가를 채웠고, 단가 표지가 없어 unit_price_cleared가 지웠으며, 계산된 품목 금액이 말한 숫자가 아니라 item_amount_invented가 그 금액과 맞던 총액을 지웠다 → 금액만 맞음→틀림. 검증은 품목 초과 400이라 어차피 저장되지 않는다 (3회 모두).

### 단계별 시간 (ms, 문장별 → p50 / p95)

| 단계 | 근거 | p50 | p95 | 최대 |
|---|---|---|---|---|
| 정규화 | 이 PC 반복 측정 | 0.08 · 0.09 · 0.09 | 0.18 · 0.29 · 0.21 | 0.19 · 0.32 · 0.24 |
| AI (processing_time_ms) | 저장값 | 1269 · 1281 · 1248 | 1664 · 1705 · 1953 | 3521 · 1868 · 2374 |
| AI (wall_time_ms) | 저장값 | 1300 · 1306 · 1262 | 1694 · 1744 · 1969 | 3631 · 1899 · 28349 |
| 후처리 | 이 PC 반복 측정 | 0.11 · 0.13 · 0.12 | 0.22 · 0.28 · 0.33 | 0.28 · 0.46 · 0.44 |
| 검증 | 이 PC 반복 측정 | 0.18 · 0.21 · 0.24 | 0.37 · 0.58 · 0.44 | 0.41 · 0.62 · 0.49 |
| 합계 (정규화+AI wall+후처리+검증) | 합 | 1300.45 · 1306.89 · 1262.97 | 1694.63 · 1745.38 · 1969.98 | 3631.36 · 1900.29 · 28349.07 |

- 합계 / 5초: p50 26.0% · 26.1% · 25.3% · p95 33.9% · 34.9% · 39.4%
- 5초 초과 문장: 0/60 · 0/60 · 1/60 — r3 g-dev-006 (wall 28349 ms · processing None · 오류 InternalServerError)
- AI wall_time_ms는 실행 당시 호출 앞뒤로 잰 값(파서 안의 정규화 포함)이라 합계는 정규화를 약간 겹쳐 센다 (정규화는 1ms 미만).

## seed2 (Seed, 140건)

- AI 출력: gpt-4o-mini · 프롬프트 `configs/prompts/parse_llm_system_p3.txt` · 측정 2026-10-23T15:39 · 2026-10-23T15:46 · 2026-10-23T15:54
- 후처리 버전(지금 코드): pp-1.5
- ⚠ 정규화가 바뀐 문장 4건 (seed2-010, seed2-027, seed2-031, seed2-069): AI는 옛 글자를 받았고 후처리·검증은 지금 글자로 돌렸다. 아래 표는 포함, 제외 수치는 표 밑에 따로.

### 단계별 기여도

| 지표 | (A) AI 원 출력 | (B) +후처리 | (C) +검증 |
|---|---|---|---|
| 거래 전체 정확도 | 82/96 · 82/96 · 81/96 | 83/96 · 83/96 · 82/96 | 83/96 · 83/96 · 82/96 |
| 금액 Exact | 114/123 · 116/123 · 115/123 | 118/123 · 120/123 · 118/123 | 118/123 · 120/123 · 118/123 |
| 필수 F1 | 95.38% · 95.46% · 95.30% | 95.46% · 95.63% · 95.38% | 95.46% · 95.63% · 95.38% |
| 정상 문장 틀린 값 저장(200) | 14 · 14 · 15 | 13 · 13 · 14 | 7 · 8 · 7 |
| 추가 확인 문장 묻지 않고 저장(200) | 44 · 44 · 44 | 44 · 44 · 44 | 1 · 1 · 2 |
| 되묻기(400) | 0 · 0 · 0 | 0 · 0 · 0 | 49 · 48 · 49 |
| 외상 잘못 확정 | 9 · 9 · 9 | 0 · 0 · 1 | 0 · 0 · 1 |

정규화 바뀐 문장 제외 (136건) — A: 거래 80/93 · 80/93 · 80/93, 금액 111/119 · 112/119 · 112/119 / B: 거래 81/93 · 81/93 · 81/93, 금액 115/119 · 116/119 · 115/119 / C: 거래 81/93 · 81/93 · 81/93, 금액 115/119 · 116/119 · 115/119

### 후처리 규칙별 (applied 라벨, A→B 거래 판정)

| 규칙 | 적용 | 틀림→맞음 | 맞음→틀림 | 변화 없음 | 추가 확인 문장(거래 판정 없음) | 금액 틀림→맞음 | 금액 맞음→틀림 |
|---|---|---|---|---|---|---|---|
| `credit_cleared_negated` | 2 · 2 · 1 | 0 · 0 · 0 | 0 · 0 · 0 | 0 · 0 · 0 | 2 · 2 · 1 | 0 · 0 · 0 | 0 · 0 · 0 |
| `credit_cleared_no_word` | 11 · 11 · 11 | 0 · 0 · 0 | 0 · 0 · 0 | 1 · 1 · 1 | 10 · 10 · 10 | 2 · 2 · 2 | 0 · 0 · 0 |
| `credit_cleared_partial_payment` | 1 · 1 · 1 | 0 · 0 · 0 | 0 · 0 · 0 | 0 · 0 · 0 | 1 · 1 · 1 | 0 · 0 · 0 | 0 · 0 · 0 |
| `item_amount_invented` | 7 · 6 · 7 | 1 · 1 · 0 | 1 · 1 · 1 | 2 · 1 · 3 | 3 · 3 · 3 | 1 · 1 · 1 | 2 · 1 · 2 |
| `total_restored_spoken` | 6 · 5 · 6 | 2 · 2 · 2 | 0 · 0 · 0 | 1 · 1 · 1 | 3 · 2 · 3 | 5 · 4 · 5 | 0 · 0 · 0 |
| `total_restored_stated` | 1 · 1 · 0 | 0 · 0 · 0 | 0 · 0 · 0 | 0 · 0 · 0 | 1 · 1 · 0 | 1 · 1 · 0 | 0 · 0 · 0 |
| `unit_price_cleared` | 70 · 67 · 71 | 2 · 2 · 2 | 1 · 1 · 1 | 54 · 51 · 54 | 13 · 13 · 14 | 5 · 4 · 5 | 2 · 1 · 2 |
| `unit_price_to_amount` | 6 · 5 · 6 | 1 · 1 · 2 | 0 · 0 · 0 | 2 · 2 · 1 | 3 · 2 · 3 | 3 · 2 · 4 | 0 · 0 · 0 |

- 한 문장에 규칙이 여럿 붙으면 각 규칙에 같은 변화를 센다.
- 거래 틀림→맞음 ID: seed2-021, seed2-023 · 맞음→틀림 ID: seed2-041
- 금액 틀림→맞음 ID: seed2-021, seed2-023, seed2-096, seed2-101, seed2-105, seed2-114 · 맞음→틀림 ID: seed2-041, seed2-093

### 따로 볼 문장 (원인)

- **seed2-041** (정상 구조화 가능, 되물을 칸: 없음) — r1: 후처리로 거래 맞음→틀림, 후처리로 금액 맞음→틀림 · r2: 후처리로 거래 맞음→틀림, 후처리로 금액 맞음→틀림 · r3: 후처리로 거래 맞음→틀림, 후처리로 금액 맞음→틀림
  - r1: AI 외상=True · 거래 종류=sale · 규칙 unit_price_cleared, item_amount_invented · 바뀐 값 total_amount: 13100 → None; items[0].unit_price: 5800 → None; items[0].amount: 11600 → None; items[1].unit_price: 7300 → None · 검증 400 MISSING_REQUIRED_FIELD ['total_amount']
  - r2: AI 외상=True · 거래 종류=sale · 규칙 unit_price_cleared, item_amount_invented · 바뀐 값 total_amount: 13100 → None; items[0].unit_price: 5800 → None; items[0].amount: 11600 → None; items[1].unit_price: 7300 → None · 검증 400 MISSING_REQUIRED_FIELD ['total_amount']
  - r3: AI 외상=True · 거래 종류=sale · 규칙 unit_price_cleared, item_amount_invented · 바뀐 값 total_amount: 13100 → None; items[0].unit_price: 5800 → None; items[0].amount: 11600 → None; items[1].unit_price: 7300 → None · 검증 400 MISSING_REQUIRED_FIELD ['total_amount']
  - 원인: AI가 첫 품목의 품목 금액을 단가로 읽고 품목 금액을 수량×단가로 계산했다. 문장에 단가 표지가 없어 unit_price_cleared가 단가를 지웠고, 계산된 품목 금액은 말한 숫자가 아니라 item_amount_invented가 그 금액과 함께 말한 합계(총액)까지 지웠다 (다른 품목 금액은 말한 숫자라 총액을 남기는 예외에 해당하지 않음). → 검증은 총액 누락 400. 틀린 값이 저장되지는 않지만 맞던 총액이 되묻기로 바뀌었다 (3회 모두).
- **seed2-061** (추가 확인 필요, 되물을 칸: credit) — r3: (C)까지 외상 잘못 확정
  - r3: AI 외상=False · 거래 종류=sale · 규칙 없음 · 바뀐 값 없음 · 검증 200
  - 원인: 정답은 외상 여부를 되물어야 하는 문장이다 (외상 말이 부정으로만 나온다, Seed v2 PM 10/8 부정 표현). r3에서 AI가 credit=false로 답했다. 후처리 pp-1.3.1은 부정어만 있을 때 AI가 부정된 쪽(외상)을 고른 경우만 지우므로(credit_cleared_negated) false는 그대로 남고, 검증은 외상 칸이 차 있어 200으로 통과했다. r1·r2는 AI가 true로 답해 credit_cleared_negated가 지우고 400으로 되물었다. → 부정어만 있는 외상 표현에서 후처리 규칙과 Seed v2 정답이 어긋나는 곳.
- **seed2-093** (정상 구조화 가능, 되물을 칸: 없음) — r1: 후처리로 금액 맞음→틀림 · r3: 후처리로 금액 맞음→틀림
  - r1: AI 외상=False · 거래 종류=purchase · 규칙 unit_price_cleared, item_amount_invented · 바뀐 값 total_amount: 534600 → None; items[0].unit_price: 22 → None; items[0].amount: 242000 → None · 검증 400 MISSING_REQUIRED_FIELD ['total_amount']
  - r3: AI 외상=False · 거래 종류=purchase · 규칙 unit_price_cleared, item_amount_invented · 바뀐 값 total_amount: 534600 → None; items[0].unit_price: 22 → None; items[0].amount: 242000 → None · 검증 400 MISSING_REQUIRED_FIELD ['total_amount']
  - 원인: r1·r3에서 AI가 용량이 붙은 품목명과 팩 수를 무게 수량·단가로 잘못 풀고 품목을 둘로 나눴다 (거래는 (A)부터 틀림). unit_price_cleared가 단가를 지우고 item_amount_invented가 계산된 품목 금액과 함께 맞던 총액(말한 숫자)을 지웠다 → 금액만 맞음→틀림, 검증은 총액 누락 400. r2는 AI가 품목 하나로 답해 규칙이 붙지 않았다.

### 단계별 시간 (ms, 문장별 → p50 / p95)

| 단계 | 근거 | p50 | p95 | 최대 |
|---|---|---|---|---|
| 정규화 | 이 PC 반복 측정 | 0.1 · 0.09 · 0.09 | 0.22 · 0.19 · 0.23 | 0.28 · 0.29 · 0.29 |
| AI (processing_time_ms) | 저장값 | 1437 · 1510 · 1511 | 2094 · 1968 · 2229 | 4702 · 2432 · 3107 |
| AI (wall_time_ms) | 저장값 | 1476 · 1536 · 1536 | 2128 · 1999 · 2266 | 4950 · 2458 · 3128 |
| 후처리 | 이 PC 반복 측정 | 0.17 · 0.16 · 0.16 | 0.33 · 0.27 · 0.32 | 0.4 · 0.41 · 0.43 |
| 검증 | 이 PC 반복 측정 | 0.21 · 0.2 · 0.19 | 0.44 · 0.42 · 0.45 | 0.54 · 0.57 · 0.58 |
| 합계 (정규화+AI wall+후처리+검증) | 합 | 1476.26 · 1536.51 · 1536.54 | 2128.59 · 1999.94 · 2266.74 | 4950.53 · 2458.43 · 3128.94 |

- 합계 / 5초: p50 29.5% · 30.7% · 30.7% · p95 42.6% · 40.0% · 45.3%
- 5초 초과 문장: 0/140 · 0/140 · 0/140
- AI wall_time_ms는 실행 당시 호출 앞뒤로 잰 값(파서 안의 정규화 포함)이라 합계는 정규화를 약간 겹쳐 센다 (정규화는 1ms 미만).

## 정규화 기여도 (참고값 — 인용, 재측정 아님)

출처: `eval/results/ko-numeral-off-on-20261001.md` (Dev 60건 · gpt-4o-mini · 대표 실행 · ko-num-1.1 · 2026-10-01). AI를 다시 불러야 잴 수 있어 이번(0원)에는 재측정하지 않았다.

| 지표 | 정규화 off | 정규화 on | 차이 |
|---|---|---|---|
| 거래 전체 정확도 | 23/42 (54.76%) | 40/42 (95.24%) | +17건 |
| 금액 Exact | 39/56 (69.64%) | 55/56 (98.21%) | +16건 |
| 필수 F1 | 89.94% | 98.78% | +8.84%p |
