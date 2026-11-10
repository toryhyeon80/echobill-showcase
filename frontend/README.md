# Frontend local workflow

## Run

1. Start the backend on port 8000: `uvicorn app.main:app --reload`.
2. In `frontend/`, run `npm ci`, then `npm run dev`.
3. Open `http://localhost:5173/record`. Development builds may use the existing dev token; production uses HttpOnly cookies and `withCredentials`.
4. For Kakao login, copy `.env.example` to `.env.local` and set the public Kakao REST API app ID and registered callback URL. Never put a client secret in `VITE_*`.

Session restore uses `GET /api/v1/auth/me` from the current backend. Network outages retain identity; 401 asks for login. OAuth state is random and checked once before exchanging the code. First login uses `signup_required` and explicit consent before calling `/auth/signup`. Signup/consent contracts are generated from the checked-out backend OpenAPI; document versions come from the v1.0 documents shown in the UI.

## Input and recovery

### 2026-10-14 FE delivery

- Current remaining review order: #131 ledger/payment → #132 tax/account. #121/#122 and #125/#126 are historical Drafts; the final pair preserves the verified FE source on current main without merge conflicts. Agents do not merge or deploy production.
- The final account screen connects AI consent PATCH, masked business details, and explicit logout. Consent values change only after server acknowledgement; uncertain retries preserve their value and key.
- Mock tax issuance requires a confirmed entry and registered business details. Uncertain POST retries keep the key; status recovery uses GET. Business numbers and registration retry keys stay in dialog memory.
- Local verification: FE 52 tests, lint, production build, OpenAPI type freshness; BE 1,981 passed / 4 environment-dependent skips; Golden 60-record validation and generated-file freshness. No backend or Golden source changes are included in these FE PRs.
- Claude Code rules/general/security review found two record recovery bugs and a ledger response-order issue; fixes were reviewed again. knip newly orphaned source/exports were removed, with custom tests/scripts and generated types excluded; jscpd reports 0 exact clones.
- Synthetic browser checks: four-question repair → unconfirmed candidate; explicit confirmation; payment response loss → same-key retry with balance 20,000 → 19,000; business registration → mock request loss → retry → GET issued; consent response loss → locked original value → acknowledged retry; signup mandatory checks/v1.0 document/Escape/retry/completion.
- Remaining: Android/iPhone Kakao login and cookies, actual microphone/STT/TTS, keyboard and Safe Area, production repair/idempotency smoke. The synthetic checks never call Kakao, STT, production payments, or a tax service.

- Text and recordings create candidates. Only explicit confirmation writes the ledger.
- `/record` records audio in memory, offers playback and explicit upload, then calls `/api/v1/ledger/parse-audio`. Microphone tracks stop when hidden or unmounted. Audio is never written to browser storage.
- Audio timeout defaults to 25 seconds per BE agreement (STT 10 + LLM 5 seconds, with time for upload/network overhead). `VITE_AUDIO_TIMEOUT_MS` values below 25000 fall back to 25000. Text `/parse` keeps the 15-second timeout. If a deployment already overrides the audio timeout, set its build-time value to 25000 and rebuild to match this agreement.
- 400/504 audio failures offer text/manual correction. STT-success 400 responses display the optional `utterance_text` transcript (DL-29); other failures do not invent an original utterance.
- Uncertain responses reuse the idempotency key. Payment inputs remain locked until retry or explicit start-over. Start-over reloads settlement state and warns that an existing server payment is not cancelled.
- Unsent text and candidate edits stay in localStorage per the PM decision. Legacy timestamped text drafts migrate from sessionStorage without extending their expiry. Drafts expire after seven days. Logout clears drafts, pointer, OAuth state and retry keys. Switching users clears previous drafts.

## Checks

Run `npm test`, `npm run lint`, and `npm run build`. Vite 6 follows the already-merged main dependency baseline; this change does not introduce another major upgrade.

`/tests/browser-fixture.html?reset=1&credit=null` uses visibly labelled synthetic data and an Axios mock adapter. It makes no real login, payment or audio requests, and is not included in the production entry point. It supports reload/apply edits, payment response-lost/retry and `expired=true` candidate expiry.

Local browser checks: 320px confirm layout, explicit credit choice, quantity edit/total calculation, leave confirmation, reload/apply edits, confirm-to-ledger and response-lost payment retry without duplication.

Still required on Android/iOS KakaoTalk: microphone denial, record/play/upload, background/swipe-close recovery, system large text, keyboard/CTA overlap, cookie expiry and actual 400/504 responses. `/recording-check` provides microphone diagnostics without upload. Use dummy speech for checks. Desktop mock checks do not establish real KakaoTalk behavior.

## API types

With the backend running, use `npm run api:types` to refresh `src/types/generated.ts` and review its diff. `npm run api:types:check` compares types with the local backend without a running server. Set `API_TYPES_PYTHON` to a Python environment with backend requirements installed. Compare with current main after integrating the stacked PRs.

## Final FE regression checks

The development fixture supports `audioMode=success|empty|denied|oversize` without a real microphone, and `voiceFields=qty,total` is its default capability set. Normal recording must automatically finish at 30 seconds and reach an unconfirmed candidate. Empty/denied recordings must show a recovery action. `answerMode=number-only` returns a quantity without inventing a unit.

Use `page=my&businessMode=registered` to verify the existing business name in the edit sheet; `businessMode=load-failed` injects one failed read and a slow save. Verify that saving recovers the page and logout still works. `consentMode=lost` checks locked consent until replay succeeds. `noticeAt=payments&noticeStatus=409&noticeCode=IDEMPOTENCY_IN_PROGRESS` checks that dismissing the notice and returning to the ledger never enables a new payment while the old request is pending. `noticeAt=ledger` or `noticeAt=unpaid` checks that a failed first load is not shown as an empty ledger or zero unpaid balance. `delayMs=1000` shows loading states.

Real Kakao login/cookies, STT transcription, TTS, keyboard/Safe Area and personal 20-sentence recordings remain separate device tasks. A successful synthetic run is not evidence that those tasks are complete.


### 10/15 마이크 권한 대기·저장소 제한 오류 수정

- 마이크 연결 대기를 음성 분석 화면과 분리했다. 권한 요청은 15초 후 안내로 전환하며, 연결 중에도 취소·글자 입력이 가능하다. 시간 초과·취소·화면 종료 후 늦게 승인된 스트림은 정지하고, 이전 요청이 새 녹음을 중단하지 못하게 했다.
- 권한 요청 중 `visibilitychange`는 실제 녹음 중 이탈과 구분한다. 실제 녹음 중 숨김·`pagehide`에서는 중단하며, 숨겨진 화면에서 승인이 끝나도 녹음을 시작하지 않는다. `/recording-check`에도 요청 취소·늦은 스트림 정리를 적용했다.
- 브라우저 저장소가 `SecurityError`·용량 초과로 실패하면 같은 페이지의 메모리에서 초안·멱등 키를 유지하고 보관 제한을 안내한다. 로그아웃·사용자 변경 시 이 메모리도 정리한다. OAuth state를 영구 보관할 수 없으면 외부 로그인으로 이동하기 전에 안내한다. 운영 JWT를 JS 저장소에 추가하지 않았다.
- 장부 조회 실패를 무한 로딩·0원으로 표시하지 않는다. 녹음·후보 복구·후보 거절 모달의 포커스 관리를 보완하고, 비로그인 기록을 실제 빈 장부와 구분했다. 빈 녹음·5MB 초과도 재시도·글자 입력 안내로 연결한다.
- 자동 검증: `npm test` 59건, `npm run lint`, `npm run build`, `npm run api:types:check` 통과. 타입 대조 시 기존 BE 가상환경을 `API_TYPES_PYTHON`으로 지정한다.
- 가상 데이터 브라우저 확인: `npm run dev` 후 `/tests/browser-fixture.html?page=record&reset=1&micMode=pending`에서 연결 대기·15초 후 안내·글자 입력을 확인한다. `micMode=permission-hide`는 권한 요청 중 화면 숨김·복귀 후 녹음→후보 확인을, `storageBlocked=true`는 저장소 제한 안내와 텍스트 후보 생성을 시험한다. `page=ledger&ledgerFailure=true`는 조회 실패 안내를 닫은 뒤에도 로딩이 남지 않는지 확인한다. 실제 마이크·STT·로그인·입금 호출은 없다.
- 네이버·카카오 앱 실기기 재현 및 운영 배포 후 재시험은 별도다. 기존 피드백의 정확한 네이버 앱 원인은 아직 확정하지 않았다.

### 10/15 추가 점검 — 응답 순서·발행 확인·로그인 재시도

- 정산 상세는 마지막으로 선택한 거래처의 응답만 표시한다. 이전 요청의 늦은 성공·실패는 무시하고, 닫기·입금 화면 열기·사용자 변경·이탈 시에도 이전 요청을 무효화한다.
- 모의 발행 초기 조회가 실패하면 미등록·빈 장부로 표시하지 않는다. 조회 완료 상태와 로딩·오류를 구분한다. 발행 확인 대상은 사용자와 함께 보관하며, 조회 월·사용자·재조회가 바뀌면 확인창을 닫는다. 현재 사용자와 다른 대상은 제출하지 않는다.
- 저장소가 잠시 제한됐다가 복구되면 현재 OAuth state의 실제 저장 성공 여부로 로그인 재시도를 허용한다. 이전 실패 이력만으로 계속 차단하지 않는다.
- 검증: 테스트 60건·lint·build·API 타입 대조 통과. 브라우저 가상 데이터에서 느린 거래처→빠른 거래처 연속 선택 뒤 4초가 지나도 마지막 선택 유지, 모의 발행 조회 500 후 허위 빈 상태 없음, 발행 확인 중 세션 만료 시 로그인 안내만 남는 것을 확인했다.
- 재시험: `?page=tax&reset=1&noticeAt=tax&noticeStatus=500`, `?page=tax&reset=1&businessRegistered=1&sessionExpiry=1`을 사용한다. 실제 로그인·입금·발행은 호출하지 않는다.

### 업로드 브랜치 검증

최신 main 1a0b678 기준으로 이번 오류 수정만 이식했다. 기존 main의 입금 재시도·정산 응답 순서·날짜 검증·가입 타입을 유지했으며, 기존 테스트를 포함한 67건·lint·build·API 타입 대조를 통과했다. 백엔드·CI·디자인 문서·생성 타입·의존성 변경은 없다. knip는 기존 항목과 자체 실행 스크립트/테스트 오탐만, jscpd는 0 exact clones를 보고했다. 네이버/카카오 실기기 QA는 별도다.

### 추가 응답 순서 점검

- 음성 제출 후 화면을 나가 새 텍스트 후보를 만든 뒤 이전 음성 응답이 도착하면 새 후보가 바뀌는 문제를 재현했다. 후보 revision을 확인해 그사이 후보 생성·버리기·복구가 있었으면 이전 응답을 적용하지 않는다. 새 작업이 없을 때의 이탈 후 복구는 유지한다. 회귀 테스트 2건을 추가했다.
- 마이페이지 첫 사업자 조회를 지연시키고 새 상호를 저장한 뒤 이전 조회를 완료하면 새 표시가 이전 상호로 바뀌는 문제를 재현했다. 저장 시작 시 이전 사업자 조회의 성공·실패를 무효화했다.
- 세금계산서 공급자 [수정] 시 기존 상호를 초기값으로 넘긴다. 사업자번호는 계속 빈칸으로 시작한다.
- 수정 전후 모두 가상 API·녹음으로 확인했으며 실제 거래·사업자 정보를 사용하지 않았다. 자동 테스트는 69건 통과했다.

### 10/15 최신 전달 지시 후속 — 최초 진입·헤더·시작 속도

- 긴급 제출 버튼은 선행 PR #139로 분리했다. 이 후속 작업은 #139 뒤에 검토한다.
- 최초 보호 화면은 세션 확인을 기다린다. 401이면 로그인으로 이동하고, 서버·네트워크 오류면 재시도 안내를 보여 준다. 작성 초안은 로그인 이동만으로 지우지 않는다.
- 기록 홈의 초기 장부·미수금 GET을 `/auth/me`와 병렬로 시작한다. 메모리 결과는 인증된 사용자·세션 세대·조회 월이 일치할 때만 사용한다. 시작 조회의 401은 인증 확인을 대신하지 않으며, 일반 요청의 세션 만료 처리는 유지한다.
- 공통 `ui/AppHeader`는 48px 행 + 위 safe area, 좌우 20px과 오른쪽 동작 영역을 갖는다. 공통 간격 토큰과 문단 초기화를 운영·미리보기 모두 사용한다. 세금 화면의 월 선택은 헤더 아래에 놓는다.
- `/design?compare=1&screen=home`으로 개발용 선택 도구를 숨기고 같은 크기로 비교할 수 있다. `screen`은 login·welcome·confirm·ledger·tax·mypage 등 기존 화면 키를 사용한다. 앱과 브라우저 fixture 모두 같은 글꼴을 사용한다.
- Pretendard 1.3.9 dynamic subset으로 교체하고 CDN preconnect를 추가했다. `public/_headers`는 해시 파일인 `/assets/*`만 1년 immutable 캐시하며 API·HTML에는 적용하지 않는다.
- 검증: FE 74건, lint·운영 build·OpenAPI 타입 대조·Golden 생성물 일치. BE 2,341건 통과, 환경 전용 4건 제외. knip 새 경고 없음(기존 경고·테스트 오탐 유지), jscpd 중복 0건. Claude CLI 부재로 규칙·일반·인증 안전성 자체 점검.
- 모바일 Lighthouse 12 단일 전후 측정: 성능 67→78, FCP 3.62→3.60초, LCP 15.61→4.23초, TBT 24→0ms, CLS 0.00095→0.00102, 글꼴 전송량 2,345,398→218,944바이트. 원격 main b6ec7d5 빌드와 변경 빌드를 localhost에서 비교했으며, 두 환경 모두 실제 계정 없이 API별 응답을 650ms로 고정했다. 운영 성능 수치·반복 측정 중앙값이 아니다.
- 가상 녹음에서 권한창이 화면을 가렸다가 복귀해도 시작되며 30초 후 자동 종료·후보 확인으로 이동함을 확인했다. 320px 화면 가로 넘침 없음. 네이버·Android/iPhone 카톡의 실제 권한 반복 현상, 실제 STT·TTS·쿠키 검증은 별도다.
- 검토 순서: [긴급 버튼 #139](https://github.com/toryhyeon80/echobill/pull/139) → [로그인·간격·시작 속도 #141](https://github.com/toryhyeon80/echobill/pull/141). 후속 PR도 전체 CI를 위해 main을 대상으로 하며, #139가 먼저 머지되면 선행 버튼 차이는 자동으로 빠진다.

### 10/16 미사용 코드 검사 정리

- `knip.jsonc`는 자체 테스트·브라우저 fixture·CSS 진입 관계를 포함한다. 자동 생성 OpenAPI 파일의 미사용 export·타입만 예외로 두며, 일반 소스의 미사용 경고는 계속 검사한다.
