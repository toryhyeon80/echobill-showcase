# Golden 채점 명세 v1.4 (2026-10-05 추가 확인 적합성 정식 연결 · PM 승인 DL-27 · 10/8 산술 모순 보완 필드)

> 적용 대상: 개발용 Golden과 최종 Golden의 장부 구조화 결과  
> 근거: `data/D03_D04_결정안.md` §0.1·§0.2, D03-5~D03-11  
> 실행 코드: `eval/scoring.py`, `eval/run_baseline.py`

## 1. 평가 트랙

### 정상 구조화 가능

- **거래 전체 정확도:** 필수 필드가 모두 일치한 거래 수 / 정상 구조화 가능 거래 수. 검증 응답(`validation_response`)이 400이면 필수 필드가 맞아도 오답이다(v1.3 보완, 기획서 §8.3). 검증 응답이 없는 예측(옛 방식·`--attach-validation` 없이 채점)은 후보만 비교하므로 숫자가 다를 수 있다
- **금액 Exact Match:** `total_amount`가 정확히 일치한 수 / 정답 총액이 있는 전체 거래 수
- **필수 필드 F1:** 정상 구조화 가능 거래의 값을 모두 합산한 micro F1
- **선택 필드 오류:** 거래 전체 정확도와 분리한 품목별·필드별 FP 건수

필수 필드는 `counterparty`, 각 품목의 `name`·`qty`, `total_amount`, `credit`이다. 생성 실패도 분모에 남기며 정답이 있는 필수 값을 FN으로 집계한다.

### 추가 확인 필요

**추가 확인 적합성:** 예측 후보를 BE 공통 검증 모듈(`validate_candidate`)에 넣어 얻은 응답(`validation_response`)이 HTTP 400이고, `error_code`가 기대 오류 코드와 같고, `problem_fields`가 필수 보완 필드(`required_clarifications`)와 집합으로 같고, `partial_candidate`에 **지어낸 필수 값이 없으면** 적합이다(v1.3). 지어낸 필수 값 = `counterparty`·`total_amount`·`credit`, 대응된 품목의 `name`·`qty`가 값이 있는데 정답과 다름, 또는 정답에 없는 품목. 선택 필드(`unit`·`unit_price`·`amount`)는 여기서 보지 않고 별도 건수로 센다. `partial_candidate`가 없으면(MULTIPLE·EXCEED) 지어낸 값 없음이다. 검증 응답은 `eval/validation_bridge.py`가 `/parse`와 같은 방식으로 만든다(`score.py --attach-validation`, `run_baseline.py`는 측정 때 저장). 검증 응답이 없는 실행은 `N/A`로 보고한다. 후보에서 값이 `null`인지만 보고 성공으로 간주하지 않는다.

보고 전용(채점 규칙 아님): **외상 잘못 확정**(보완 필드에 `credit`이 있는데 200으로 credit 값을 확정한 건수·ID), **정상 건 중 400**(불필요한 보완 요청 건수·ID — 거래 전체 정확도에서도 오답), 선택 필드 오류(§1 선택 필드 FP·누락과 같은 기준).

## 2. 정규화와 품목 대응

- `counterparty`, `items[].name`: 앞뒤와 내부의 모든 공백을 제거한 뒤 완전 일치
- `qty`: `Decimal` 수치 비교. `5`와 `5.0`은 같고 `null`과 `0`은 다름
- `total_amount`: 정수 완전 일치
- `credit`: `true`, `false`, `null`을 서로 다른 값으로 비교
- 키 누락: `null`로 간주
- 품목: 정규화한 `name`으로 순서와 무관하게 1:1 대응
- 동명 품목: 수량 일치 쌍을 먼저 확보하여 수량 일치 개수가 최대인 1:1 대응. 남은 동명끼리는 등장 순서로 대응. 동률에 선택 필드 최적화는 적용하지 않음.
- 이름이 대응되지 않은 정답 품목: 값이 있는 `name`·`qty`를 각각 FN
- 이름이 대응되지 않은 예측 품목: 값이 있는 `name`·`qty`를 각각 FP

잘못된 비-null 값은 FP 1과 FN 1로 집계한다. 정답과 예측이 모두 `null`이면 F1 분모에 넣지 않는다. 모든 평가 대상 값이 없어 분모가 0이면 0%가 아닌 `N/A`다.

공백 제거 덕분에 일치한 이름은 필드 건수와 거래 건수를 별도로 보고한다.

## 3. 선택 필드

대상은 `unit`, `unit_price`, `items[].amount`다.

- 정답이 `null`인데 비-null 값을 생성: FP 1
- 정답과 다른 비-null 값을 생성: FP 1
- 정답이 `null`이고 예측도 `null` 또는 키 누락: 오류 없음
- 정답이 비-null인데 예측이 `null`: 선택 필드 FP에는 포함하지 않고 `optional_field_missing`으로 별도 집계
- 집계 단위: 품목별 × 필드별

대응되지 않은 추가 예측 품목의 비-null 선택 필드도 각각 FP로 센다.

## 4. 금액 Exact Match

정답 `total_amount`가 있는 모든 거래를 분모에 포함한다. `notes.total_source`에 따라 다음을 함께 보고한다.

- `stated`: 발화에 총액이 명시됨
- `derived`: 단가×수량 또는 품목 금액 합으로 도출됨

비교 허용 오차는 0원이다.

## 5. D03 확정 규칙

main의 결정안 §0.2 (2026-09-29) 확정에 따라 적용한다.

1. `confirm_type`은 채점 그룹이며 `expected_error_code`는 build.py가 산출한다. `validation_response`의 status_code=400, error_code 일치, problem_fields 집합 일치, partial_candidate에 지어낸 필수 값 없음을 확인한다(v1.3에서 선택 필드 근거 검사 제거 — 아래 v1.3 이력). message 문구는 비교하지 않는다.
2. 품목명 매칭에는 공백 제거 정규화를 적용한다. 이름이 틀려 대응되지 않은 품목은 정답 필수 값 FN과 예측 필수 값 FP로 각각 센다.
3. 예측 키 누락은 `null`로 간주한다.
4. 선택 필드 오류는 품목별·필드별 건수로 집계한다.

5. 자료형 오류는 `format_errors`에 ID와 경로로 기록하며 값을 null로 바꾸지 않는다.
6. 검증 오류 우선순위는 INPUT_TOO_LONG → MULTIPLE_COUNTERPARTIES_DETECTED → EXCEED_MAX_ITEMS → MISSING_REQUIRED_FIELD → AMOUNT_OUT_OF_RANGE → AMOUNT_MISMATCH다. 우선순위 판정은 BE 공통 검증 모듈 소관이며 채점기는 Golden의 기대 코드와 비교한다. 검증 응답이 없으면 추가 확인 적합성을 측정 완료로 표시하지 않는다.

이 기준이 PM 결정으로 변경되면 `scoring_version`을 올리고 과거 예측 JSONL을 새 기준으로 다시 채점한다.

## 6. 기준선 실행과 산출물

저장소 루트에서 실행한다.

```powershell
backend\.venv\Scripts\python.exe eval\run_baseline.py
```

D02 비교용 반복 실행은 `--repeat 3`을 붙인다. 실행 ID `<묶음>-r1`~`-r3`의 산출물과 함께 `<묶음>.group.json`에 거래 전체 정확도 중앙값 실행, 반복 간 편차, 실행마다 결과가 달라진 레코드 ID를 기록한다.

러너는 모든 레코드의 `split`이 `개발용 Golden`이 아니면 호출 전에 실행을 거부한다 (최종 평가용 격리, DL-25).

저장된 예측만 다시 채점할 때는 `python eval/score.py --golden <정답.jsonl> --predictions <예측.jsonl> --output <새결과.json>`을 사용한다. 정답에는 있는데 예측이 없는 ID가 있으면 실패로 섞지 않고 오류로 중단한다. 과거 40건 예측처럼 일부만 채점할 때는 `--only-predicted`를 붙이며, 제외한 ID는 결과의 `run.excluded_golden_ids`에 남는다. 정답에 없는 예측 ID도 거절한다.

`eval/results/`에 실행 ID별로 세 파일이 생성된다.

- `*.predictions.jsonl`: 각 Golden ID의 원시 후보, 오류, 지연시간, 토큰, 응답 모델 스냅샷(`response_model`·`system_fingerprint`), 전송 원문 해시(`sent_text_sha256`)
- `*.summary.json`: 실행 조건과 전체 지표
- `*.report.md`: 사람이 읽는 요약

각 레코드는 한 번 호출한다. 프롬프트·규칙 변경 후에는 개발용 Golden 전량을 새 실행 ID로 다시 측정한다. 원시 예측을 보존하므로 호출 없이 채점 규칙만 다시 적용할 수 있다.

SDK 재시도는 시도마다 제한 시간을 새로 시작하므로 `LLM_MAX_RETRIES` 기본값을 0으로 둔다. 따라서 한 번의 호출은 5초 제한 안에서 끝나거나 실패한다. 각 행의 `wall_time_ms`는 성공·실패 모두 기록하며, 5초를 넘긴 행 수를 `measurements.over_deadline`에 보고한다. 호출 직후 저장해 중단 시에도 완료된 행을 보존한다. 같은 실행 ID의 예측 파일은 덮어쓰지 않는다.

산술 모순 보완 필드는 수량·단가·총액 또는 품목 금액·총액 둘 다 적합 (v1.4, 10/8 PM)

v1.3(2026-10-05, PM 승인, DL-27 · 10/6 보완)은 추가 확인 적합성을 **오류 코드 + 필수 보완 필드 + 필수 값 지어냄 없음**으로 판정한다. v1.2까지 보던 `partial_candidate` 근거 검사 중 선택 필드 부분은 뺐다: ① MULTIPLE_COUNTERPARTIES_DETECTED·EXCEED_MAX_ITEMS는 수정 모달에 보여줄 값이 없어 `partial_candidate`가 null이므로(DL-03) 코드·필드가 맞아도 항상 오답이 됐고, ② 계산한 품목 금액·단위는 지어낸 값이 아니다(Golden 정상 정답도 계산값). 선택 필드(단위·단가·품목 금액) 오류는 기획서대로 별도 건수로 본다. 필수 값(거래처·총액·외상, 품목명·수량, 정답에 없는 품목)을 지어내면 계속 부적합이다(PM 10/6). 정상 건이 400이면 거래 전체 정확도에서 오답이다(PM 10/6, 기획서 §8.3). 버전 문자열은 v1.3 그대로이며, 10/6 보완 전 v1.3 결과 파일은 같은 이름으로 다시 만들었다. v1.2 이전 결과와 섞지 않도록 과거 실행도 v1.3으로 다시 채점한다(`*.rescored-v1.3.summary.json`).

v1.1은 credit의 숫자 0/1과 boolean을 구분하고, 오류가 있는 후보를 실패 처리하며 중복 Golden ID를 거절한다. 필드별 정확도는 전체 평가 건 중 해당 정답값이 있는 필드를 대상으로 하며 누락은 오답이다. 원시 예측에서 없는 과거 실행 메타데이터는 복원된 것으로 간주하지 않는다. `items`가 배열이 아니거나 품목이 객체가 아닌 후보는 `format_errors`에 경로를 남기고 생성 실패로 집계한다.

지연 p50·p95는 성공 호출의 최근접 순위 방식으로 계산한다. 비용은 입력 토큰 $0.15/백만, 출력 토큰 $0.60/백만으로 단순 추정하며 캐시 할인은 반영하지 않는다. 가격 기준은 실행 시점의 OpenAI GPT-4o-mini 모델 문서다.
