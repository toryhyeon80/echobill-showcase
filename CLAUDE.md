# 🛠️ EchoBill — AI 에이전트 개발 행동 강령 (CLAUDE.md)

> **[필독] 에이전트 구동 지침**
>
> 1. 이 문서의 명령·스타일·불변 규칙을 EchoBill의 **실행 최우선**으로 준수한다.  
> 2. **제품 SSOT:** 에코빌 기획서 → [`docs/DOC_CRITERIA_ALIGNMENT.md`](docs/DOC_CRITERIA_ALIGNMENT.md) → [`SPEC_MASTER.md`](SPEC_MASTER.md) → 본 `CLAUDE.md`.  
> 3. **설계 북극성:** [`docs/agent/NORTH_STAR.md`](docs/agent/NORTH_STAR.md) — 제1원리로 단순화하되 **SPEC·Must는 깨지 않는다.**  
> 4. 코드를 수정한 뒤에는 해당 방(`backend/` / `frontend/`)에 맞는 **검증(Test/Build)** 을 실행한다. 기능 단위 완료 시 [§1.5](#15-1인--소수-팀-코드리뷰-solo--ai) 루틴을 따른다.  
> 5. UI는 [`docs/design/README.md`](docs/design/README.md) · 노션 v0 프롬프트 · SPEC §4 UX 원칙을 따른다. 임의 디자인 시스템 발명 금지.  
> 6. logpick_core와 **스택·제품이 다르다** (에코빌=FastAPI+React Vite 인앱 웹). LogPick 경로·Vision·Next.js 규칙을 그대로 복사하지 않는다.

**충돌 시:** 기획서 ⊃ 기준 정리본 ⊃ SPEC_MASTER ⊃ CLAUDE.md ⊃ API/DB/WBS.

---

## 0. 시작 전 체크 · 북극성

1. 요청이 **MVP 범위**인지 ([SPEC_MASTER](SPEC_MASTER.md) §1 하지 않는다).  
2. 스키마·API 변경이면 **D04/D09 미결**인지 확인 — 추측으로 freeze 금지, PM 이슈로 남길 것.  
3. WBS·구 VoiceLedger·노션 초안과 SPEC이 다르면 **SPEC·기준 정리본**을 따른다.  
4. 설계·UX·파서 **전략**을 제안할 때 [`docs/agent/NORTH_STAR.md`](docs/agent/NORTH_STAR.md)를 따른다 — 유추가 아닌 제1원리, 단 **Must/SPEC 가드레일**.  
5. 구현·버그픽스는 YAGNI · 기존 패턴 우선 (매 작업마다 철학 에세이 금지).

---

## 1. 빌드 및 테스트 자동화 (Build & Test)

코드를 수정한 후 **해당 디렉터리로 이동**해 아래를 실행하고 통과 여부를 판별한다. (폴더가 아직 없으면 스캐폴드 후 적용.)

### ① Backend (`backend/` — FastAPI)

```bash
cd backend
pip install -r requirements.txt   # 또는 uv/poetry 합의안
uvicorn app.main:app --reload     # 엔트리 경로는 스캐폴드에 맞춤
pytest
```

### ② Frontend (`frontend/` — React + Vite)

```bash
cd frontend
npm install
npm run build
npm run lint    # 스크립트 있으면
```

### ③ 데이터·AI 채점 (`data/` · 파서 테스트)

```bash
# Golden Dev 파서 회귀 (저장소 루트, API 없음 — CI와 같은 명령)
python eval/regression.py
```

기준선(`eval/regression_baseline.json`)에서 맞던 Dev 문장이 거래·금액·추가 확인 적합성 중 하나라도 틀리거나 외상 잘못 확정이 새로 생기면 실패. 새로 맞힌 문장은 경고 → 같은 PR에서 `python eval/regression.py --update-baseline` 후 기준선 파일 변경을 커밋.
기준선 갱신으로 잃는 ID(지금 틀리는 기준선 정답)를 받아들이려면 PR 본문에 ID·이유를 적고 PM 승인 후 `--update-baseline` (PM #68 — 지금 도구는 잃는 ID가 있으면 갱신을 거부하므로, 받아들이는 방법은 후속).

외상 반대 저장(`credit_flip_saved` — 현금→외상·외상→현금)도 외상 잘못 확정처럼 기준선에 없던 ID가 어느 방향이든 생기면 실패 (PM 10/8).

E2E 스모크(구현 후): `/parse` → feedback confirm → ledger → payment FIFO → tax mock.

### ④ 클린 코드 검사 (PM 10/12)

AI 도구(Claude·Codex 등)로 만든 코드는 안 쓰는 코드·복붙·디버그 잔재가 남기 쉽다. 도구와 상관없이 같은 검사를 건다. **지금은 CI 밖 일회성 실행**(레포에 의존성 추가 없음), 시연 뒤 CI에 경고로 넣고 정리되면 머지 차단으로 올린다.

```bash
# frontend/ — 안 쓰는 파일·export·의존성, 8줄 이상 중복
npx -y knip@5 --no-progress --reporter compact
npx -y jscpd@4 src --min-lines 8 --reporters console --silent

# backend/ — ruff는 로컬 개발 도구로만 설치 (requirements.txt에 넣지 않음)
pip install ruff && ruff check app
```

| 구분 | 기준 |
|---|---|
| **머지 전 수정** (이번 PR이 새로 만든 것만) | 안 쓰는 파일·export·의존성 · 8줄 이상 복붙(공통 모듈·훅으로) · 근거 없는 `any`·`# type: ignore` · 남은 `console.log`·디버그 `print` · 주석 처리된 코드 덩어리 · 안 쓰는 import·변수 |
| **나중에** (막지 않음) | 복잡도 경고(긴 함수·분기 많음) · import 순서·표기 같은 자동 수정 스타일 · 300줄 넘는 파일(§3①-b 소프트 가이드) |
| **오탐으로 무시** | `tests/`(자체 실행 스크립트), `src/types/generated.ts`(OpenAPI 자동 생성) |

- 기존 코드의 경고는 이번 PR 범위가 아니면 고치지 않는다 (§3④ 최소 변경). `ruff --fix` 같은 자동 수정은 **이번 PR이 바꾼 파일에만**.
- 결과는 PR 본문 「어떻게 검증했나」에 한 줄로 (예: "knip 새 항목 0 · jscpd 0 · ruff 새 경고 0"). `/pr-review`도 같은 기준으로 본다.

---

## 1.5 1인 / 소수 팀 코드리뷰 (Solo + AI)

> BE/FE 실력이 팀에 편중되어 있어도, AI 생성 코드는 **자동 검증 + AI 리뷰 + 사람 스팟**으로 품질을 맞춘다. (logpick_core §1.5 이식)

**기능 1단위(또는 PR 1개) 완료 시 — 커밋·푸시 전**

1. **자동 검증** — §1 해당 방 명령 실행 (실패 시 커밋 금지). CI와 같은 명령이라 로컬에서 먼저 통과시킨다. 클린 코드 검사(§1④)도 함께 돌린다.  
2. **AI 리뷰** (Claude Code — 팀 플랜, 2026-10-28까지)  
   - EchoBill 규칙 대조: `/pr-review <PR번호>` (프로젝트 스킬 `.claude/skills/pr-review`, PR 올리기 전 셀프 점검은 번호 없이)  
   - 일반: `/code-review` (PR이면 `/code-review <PR번호>`)  
   - auth · 카톡 · `.env` · 외부 API(STT/LLM/팝빌 Mock) · 결제성 멱등: `/security-review` 추가  
   - `/pr-review`의 「머지 전 수정」은 고친 뒤 다시 돌려 확인  
3. **사람 스팟 체크** (~5분) — (가) 이번 요구와 일치 (나) 대량 삭제·무관 리팩터 없음 (다) `.env`·비밀 미스테이징  
4. **PR** — `main` 직접 push 금지 · Reviewer **최승현** · CI/로컬 green 후 PM 머지  
   - **PR 본문 최소 3항목:** 무엇을 바꿨나 · 어떻게 검증했나 · 범위 밖 변경(없으면 「없음」). 새 의존성을 추가했으면 이유도 적는다.  
   - **PM이 올린 PR:** 문서·테스트·Golden 데이터만 바꿨으면 CI green 후 셀프 머지 가능. `backend/`·`frontend/` 코드를 바꿨으면 해당 역할 팀원을 Reviewer로 지정한다 (자기 리뷰 금지).  
     - **동결 주간 예외 (PM 10/23, 10/30 동결까지만):** 내부 테스트 시간을 벌기 위해 PM PR은 Reviewer를 지정한 채 셀프 리뷰(`/pr-review`·`/code-review` 또는 독립 AI 리뷰) + CI green 후 먼저 머지할 수 있다(관리자 우회). 팀원은 머지 뒤 리뷰하고, 지적은 바로 수정 PR로 반영한다. 기본 꺼짐 기능을 우선하고, 운영 동작이 바로 바뀌는 PR은 본문에 영향 범위를 적는다.

배포 전 env: secrets·카카오·LLM·DB URL 체크리스트를 팀이 정하면 `docs/` 또는 `deploy/`에 둔다.

---

## 2. 문서 우선순위 · 역할 경계

### 읽기 순서

`docs/agent/NORTH_STAR.md` (설계 시) → `SPEC_MASTER.md` → `CLAUDE.md` → (충돌) `docs/DOC_CRITERIA_ALIGNMENT.md` → (UI) `docs/design/README.md` + 노션 → (계약) API/DB 명세

### 역할 → 경로

| 역할 | 주 경로 |
|---|---|
| PM · 최승현 (`toryhyeon80`) | `SPEC_MASTER.md` · `docs/` · Golden 검수 · main 머지 · 아키 골격 |
| AI · 김수경 (`gyeong061`) | `backend` 파서·`configs/` · `data/` · 채점 · Rule 폴백 |
| Backend · 최병철 (`choi0310`) | `backend/` FastAPI·SQLAlchemy·Mock·Auth·FIFO · (선택) webhook |
| Frontend · 이예령 (`hayan0308`) | `frontend/` React·Zustand·인앱 3탭 · (선택) 운영 조회 UI |

상세: [docs/TEAM_ROLES.md](docs/TEAM_ROLES.md)  
역할 밖 대규모 변경은 PR에 명시하고 PM 리뷰.  
**시작 전 사전 협의:** 다른 역할의 경로를 크게 바꾸는 작업(예: FE 담당의 `backend/` 변경)은 **착수 전에 PM·해당 담당과 범위를 맞춘다.** 담당이 부재 중이어도 같다 — 이미 열린 PR·브랜치와 같은 기능을 따로 만들지 않도록, 최신 `main`에서 출발해 작은 PR로 나눈다 (2026-10-01, #29 계기).

---

## 3. 코딩 스타일 · 견고성 (logpick_core 이식)

### ① 가독성 · 아키텍처

- 함수·클래스 상단에 **Docstring / JSDoc** (역할·입출력).  
- **단일 책임** — 한 함수 한 일.  
- 중복 비즈니스 로직은 유틸·공통 모듈·커스텀 훅으로 분리 (복붙 금지).  
- TS/프론트: `any` 남용 금지 · API 응답·Ledger 타입을 `interface`/`type`으로 명시.  
- Python: 타입 힌트 + Pydantic 모델 우선.
- **테스트 동반:** 새 API·분기·버그 수정에는 그 경우를 잡는 테스트를 같은 PR에 넣는다 (버그는 먼저 실패하는 테스트로 재현). 테스트가 어려운 FE 화면은 PR 본문에 확인 절차를 적는다.
- **새 의존성**(`requirements.txt`·`package.json`)은 표준 라이브러리·기존 패키지로 안 되는지 먼저 본다.

### ①-b 파일 분리 · 길이 (가이드 — 하드 제한 아님)

**분리 import (권장)**  
- React 컴포넌트 · 훅 · API 클라이언트 · 유틸은 **파일로 나누고 import**한다.  
- 스타일은 인라인 거대 `<style>` 몰아넣기보다 **CSS Module / Tailwind / 별도 `.css` import**를 우선한다.  
- v0 등에서 받은 단일 파일은 동작 확인 후 **화면·훅·스타일 단위로 분리**하는 것을 권장한다.

**파일 길이 (소프트 가이드)**  
- **목표: 약 300줄 근처**에서 역할이 보이면 분리 mid.  
- 한 기능(예: FIFO 정산, parse 파이프라인)이 **자연히 더 길면 허용**한다.  
- **300줄 초과 = 커밋 금지·강제 리팩터가 아니다.** 읽기·리뷰·에이전트 컨텍스트가 버거울 때만 나눈다.  
- 에이전트는 가능하면 **관련 파일만** 읽고 수정한다 (토큰·효율).

### ② 예외 · 보안

- 외부 API(STT/LLM/팝빌 Mock)·파일·DB 구간은 **`try/except` 또는 `try/catch`**.  
- **예외를 삼키지 않는다:** 예상한 구체 예외만 잡고, 로그(마스킹)를 남기고, §4 HTTP 표의 코드로 돌려준다. `except: pass`·빈 `catch {}`·서버 오류를 400으로 바꾸기 금지 (500은 500).  
- API 키·DB 비밀번호 **하드코딩 금지** → 루트/디렉터리 `.env` · 호스팅 env.  
- 로그에 사업자번호·원문 PII·토큰 평문 금지 (마스킹).  
- ORM만 사용 · raw SQL 문자열 조립 금지.  
- FE: `dangerouslySetInnerHTML` 금지.

### ③ 답변 출력 (에이전트)

- 생각 과정(CoT)을 장황한 일반 텍스트로 중복 출력하지 말 것.  
- **정제된 결과**(코드·짧은 설명·검증 결과)만 사용자에게 제시.

### ④ 최소 변경

- 요청과 무관한 리팩터·문서 양산·전체 재작성 금지.  
- 버그 수정은 **핀포인트** — 관련 없는 파일을 갈아엎지 말 것.

---

## 4. EchoBill 구현 불변 규칙

1. **candidate → 사용자 확정 전 confirmed 금지**  
2. **단가 null 허용** · 수량 임의 1 금지 · 3품목·다중 거래처 부분 저장 금지  
3. **LLM ≤5초 → Rule** · Rule 성공 **HTTP 200** · STT 복구 실패만 **503** (DL-33, 11/2 — 504는 Cloudflare가 바꿔치기)  
4. **초기 LLM = GPT-4o-mini (D01)** · Gemma/Claude 체이닝은 선택 실험 플래그 뒤  
5. **세금 = Popbill 참고 Mock** · `issued`=모의 완료 · 실국세청 표현 금지  
6. **카톡 웹훅 파싱 = 선택** · MVP 본선은 인앱 웹 입력  
7. Soft Delete = **스크립트 + audit_log + deleted_at** · Admin 삭제 UI 없음 · 입금/Mock 연결 Lock · **예외:** 사용성 테스트 동의서의 "테스트 끝난 날 실제 삭제"만 `scripts/user_purge.py`로 실제 삭제 (BE 실행 · PM 승인 · 감사 기록은 건수와 비밀값 섞은 해시 1행만, PM 10/28)  
8. 상태변경 POST: **`X-Idempotency-Key`** + JWT 소유권  
9. Sanitization 후 Pydantic  
10. 원본 오디오 STT 후 **즉시 삭제**  
11. Golden **Test**로 튜닝·Champion 판단 금지 · **Dev**만 개선 비교  
12. **95%는 Stretch** · 배포 게이트 아님  
13. 모델명·프롬프트·온도 → `configs/*.yaml` 또는 env  

### HTTP / 오류 UX

| 코드 | 언제 | FE |
|:-:|---|---|
| 200 | 후보 성공 (Rule 폴백 포함) | 확인 화면 |
| 400 | 품목초과·다중거래처·누락·산술 · 지원하지 않는 거래(매입·반품·부분 결제, `UNSUPPORTED_TRANSACTION`, DL-30) · 판매가 아닌 말(외상 갚음·지난 기록 고치기·외상 조회·합친 외상, `NOT_A_SALE` + `redirect` = `payment`·`edit`·`inquiry`·`moved_credit`, #216 — 장부 후보 저장 없음) · 꺼진 칸 음성 답(`VOICE_ANSWER_DISABLED`, DL-31 — 화면은 켜진 칸에만 🎤를 보여 사용자에게 보이지 않아야 함) | 되묻기 질문 카드(한 번에 한 칸, 큰 글씨) · 음성 답(STT) + 버튼·키패드 · 질문 읽어 주기(서버 TTS 자동 재생 + 다시 듣기, 10/26) — DL-31. 품목 초과·다중 거래처·지원하지 않는 거래는 안내 후 다시 말하기. `NOT_A_SALE`은 서버 안내 + `redirect` 화면 이동 버튼(입금 등록·장부·받을 돈·외상 옮기기), 재입력 안내 아님 |
| 401 | 토큰 없음·만료 | 재로그인 |
| 404 | 없는 데이터·**타인 데이터**·Soft Delete 건 (구분하지 않음, DL-21) | 「찾을 수 없음」 안내. 서버 로그엔 소유권 불일치를 별도 기록 |
| 409 | 같은 Idempotency 키에 다른 본문 · 7일 지난 후보 확정 (DL-22) · 확정 전 장부로 세금 Mock 발행 (`ENTRY_NOT_CONFIRMED`) · 옮겨 온 외상으로 세금 Mock 발행 (`ENTRY_NOT_TAXABLE`, 품목·거래일이 없음, PM 10/23) · 이미 확정된 보완을 같은 `repair_of`로 다시 제출 (`REPAIR_ALREADY_CONFIRMED`, PM 10/15) | 재입력 안내 (400 수정 모달과 구분). `ENTRY_NOT_CONFIRMED`는 「장부를 먼저 확정해 주세요」, `ENTRY_NOT_TAXABLE`은 서버 안내 그대로(발행 목록에서 이미 빠져 보통 안 보임), `REPAIR_ALREADY_CONFIRMED`는 장부로 안내 |
| 413 | 요청 크기 초과 (`PAYLOAD_TOO_LARGE` — 음성 약 6MB·그 외 64KB) | 「녹음이 너무 깁니다. 짧게 나눠 주세요」 → 텍스트 입력 전환. 400 모달 아님 |
| 429 | 사용자별 사용량 상한 초과 (`Retry-After` 헤더, D09 — 텍스트·음성 상한은 따로 셈) | 「잠시 후 다시 시도」 + 기다릴 시간. 400 모달·장애 화면 아님 |
| 503 | STT 등 복구 실패 · 음성 입력에서 거래처·품목·금액·외상 여부를 하나도 못 뽑음(주변 말소리만 적힘, #260) (`EXTERNAL_API_TIMEOUT`, DL-33 — 504·502는 Cloudflare가 CORS 없는 자체 오류 페이지로 바꿔 FE가 네트워크 오류로 봄) | 텍스트 입력 전환 |
| 500 | 서버 | 시스템 장애 (입력오류 위장 금지) |
| — | 네트워크 무응답 | 네트워크 분기 (400 모달 금지) |

### FE (카톡 인앱)

v0→API/Zustand 수동 연결 · Empty State · Skeleton.

**호환 가드** — 우선순위: **상태 복구 > 레이아웃(dvh/Safe Area) > 녹음 유지 시도 > History 트랩(선택)**.

1. **레이아웃:** `100dvh` · `viewport-fit=cover` · Safe Area · CTA/하단 탭 가림 방지.  
   포커스 시 `visualViewport` 또는 `scrollIntoView({ block: "center" })` (전역 강제 스크롤 남발 금지).
2. **이탈·스와이프:** 후보·작성 중 드래프트는 **local/sessionStorage 또는 Zustand persist(변경 debounce 300~500ms)**.  
   재진입 시 복구 확인. **1초 타이머 폴링 금지**. 드래프트 저장소에 JWT·사업자번호 등 PII 저장 금지 (JWT는 HttpOnly 쿠키 — DL-12, JS에서 읽지 않는다).
3. **뒤로가기:** dirty한 `/record`·confirm에서만 History soft-trap + 확인 모달  
   (“작성 중인 장부가 있습니다…”). **X/웹뷰 종료를 막으려 하지 말 것** — 복구로 대응.
4. **녹음:** 시작 제스처에서 Wake Lock 시도 · 종료 시 release.  
   미지원·백그라운드는 `visibilitychange`/`pagehide`로 중단·안내.  
   무음 media 루프는 **최후 폴백·실험 플래그만** (본선 필수 아님). 마이크 실패 → 텍스트 입력(503/음성 불안과 정합).

### AI

Thinking 모드 off · `processing_time_ms` · 프롬프트 인젝션 가드 · JSON 스키마 · 금액·외상 Rule 후처리 · Feedback은 동의·가명 후 Seed · Golden 문장 few-shot 금지.

---

## 5. 에러 발생 시 디버깅 프로토콜 (logpick_core 이식)

테스트·빌드가 `FAIL` / `Error` / 컴파일 에러면:

1. **로그 분석** — Traceback·에러 유형의 **마지막 유의미한 줄**부터.  
2. **원인 격리** — 파일·라인 특정.  
3. **핀포인트 수정** — 임의 전면 재작성 금지.  
4. **재검증** — §1 해당 방 명령을 **다시** 실행해 통과 확인.

---

## 6. Git 커밋·푸시 규칙 (logpick_core 이식 + 에코빌)

> 커밋=로컬 안전망, 푸시=원격 백업. **에이전트는 사용자가 커밋을 요청한 경우에만** `git commit` 실행.

### ① 커밋 시점 (권장 — 사용자에게 제안)

| 타이밍 | 예시 |
|---|---|
| 기능 1단위 + 테스트 통과 | parse API + pytest |
| 주간 체크리스트 1항목 | FIFO 입금 + 초과 입금 400 |
| 위험 작업 직전 | 대량 이동·schema drop·Railway 재설정 |
| 세션 종료 | 1~2시간 블록 끝 |

리듬: **30분~2시간** 또는 체크리스트 1항목. 며칠치 미커밋 금지.

### ② 푸시

- 하루 1회 이상 push 권장 (팀 합의).  
- `.env` · 오디오 원본 · 대용량은 `.gitignore` — 커밋으로 보호 안 됨.

### ③ 에이전트

- **명시적 커밋 요청 시에만** commit.  
- 기능 단위 완료·테스트 통과 시 **커밋·푸시를 사용자에게 제안**.  
- 커밋 전: `git status` · `git diff`로 `.env`·비밀·대용량 스테이징 여부 확인.  
- `main` 직접 push 금지 · `--force` / amend는 사용자 명시 시에만.

### ④ 커밋 메시지

- **언어:** 한국어 본문 가능 · **태그:** 영문 conventional  
- **타임존:** KST  
- 형식: `feat(backend): …` / `fix(frontend): …` / `docs: …` / `chore: …`  
  - 예: `feat(backend): 장부 후보 parse 및 candidate 저장`  
  - 예: `fix(frontend): 카톡 인앱 Safe Area CTA 겹침 수정`

### ⑤ 브랜치 · PR

```text
git checkout main && git pull
git checkout -b feature/<역할>-<작업>
git push -u origin HEAD
# PR Reviewer: 최승현 → PM만 merge
```

충돌 시 임의 해결 금지 → PM·페어.  
AI 파서 PR: Dev Golden 소규모 회귀 후 요청.

### ⑥ 커밋 전 최소 검증

| 디렉터리 | 검증 |
|---|---|
| `backend/` | `pytest` |
| `frontend/` | `npm run build` (+ lint 있으면) |
| `data/`·파서 | 채점/스모크 합의 명령 |

§1.5 1~3단계 통과 후 커밋.

---

## 7. 하지 말 것 (에이전트)

- voiceledger / logpick_core 코드를 조용히 복사해 “완성”으로 포장 (스택·제품 다름)  
- Admin Soft Delete 화면 · 웹훅을 MVP 블로커로 · 95%를 배포 실패로  
- SPEC과 다른 필드명 재도입 (`total_amount_krw` 등)  
- logpick_core Vision·Next.js·frontend-admin 경로 규칙을 에코빌에 강제  
- 사용자 요청 없는 git commit / push / force / amend  
- CoT 장황 출력 · 전면 재작성으로 “디버깅”  

---

## 8. logpick_core에서 가져오지 않은 것 (의도적)

| 제외 | 이유 |
|---|---|
| Vision / frontend-user(Next) / admin 멀티앱 | 에코빌 단일 FE |
| aider DeepSeek 기본 모델 강제 | 팀 도구는 Claude Code 중심 · 모델은 D01 |
| LogPick DESIGN.md 토큰 | 에코빌은 노션 v0 + SPEC UX |
| LogPick NORTH_STAR 원문 그대로 | → [`docs/agent/NORTH_STAR.md`](docs/agent/NORTH_STAR.md)로 **에코빌용 재작성** (제1원리 + Must 가드레일) |
