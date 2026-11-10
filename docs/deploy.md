# 배포 체크리스트 (에코빌)

> **구성:** FE → Cloudflare Pages · BE+DB → Railway · `main` 머지 시 자동 배포 ([SPEC §3](../SPEC_MASTER.md))  
> **담당:** FE 배포 이예령 · BE/DB 배포 최병철 · 도메인·계정·비용 결정 PM  
> **시점:** 도메인 구매 **9/30** · 카카오 앱은 임시 주소로 먼저 등록하고 커스텀 도메인은 나중에 추가(DL-21) · 1~3번은 **BE 인증 구현(2주차 초) 전**, 나머지는 첫 배포 전

환경변수 이름은 `backend/.env.example`, `frontend/.env.example`과 같다. 실제 값(비밀키 등)은 이 문서와 레포에 쓰지 않는다.

---

## 1. 도메인 — 커스텀 도메인 하나 아래 서브도메인

| 용도 | 예시 | 호스팅 |
|---|---|---|
| 인앱 웹 (FE) | `app.echobill.kr` | Cloudflare Pages |
| API (BE) | `api.echobill.kr` | Railway |

- [x] 도메인 **`echobill.kr`** 구매 — 2026-09-28, PM (DL-24). 등록대행 호스팅케이알, 네임서버는 Cloudflare
- [x] 도메인 **이전 잠금·자동 연장** 켬, 호스팅케이알·Cloudflare·Railway·GitHub·OpenAI·카카오 PM 계정 **2단계 인증** — 2026-10-06, PM. 도메인·DNS를 빼앗기면 app·api가 통째로 넘어간다
- [x] Pages·Railway에 커스텀 도메인 연결, HTTPS 확인 (2026-10-02)
- [x] 카카오 디벨로퍼스에 **로그인 Redirect URI를 커스텀 도메인으로** 등록 — 2026-10-02, `https://app.echobill.kr/auth/kakao/callback` + `http://localhost:5173/auth/kakao/callback`, 호출 허용 IP 비움. 새 콘솔의 REST API 방식은 "사이트 도메인" 등록이 필요 없다(JS SDK용)

`*.pages.dev`·`*.up.railway.app` 기본 주소로 카카오를 등록하면 주소가 바뀔 때 카카오 설정과 CORS를 모두 다시 해야 한다.

### 현재 구성 (2026-10-02)

| 주소 | 연결 | Cloudflare DNS | 비고 |
|---|---|---|---|
| `app.echobill.kr` | Cloudflare Pages `echobill` (PM 계정, Git 연결) | CNAME → `echobill.pages.dev`, **Proxied** | Pages가 자동 생성 |
| `api.echobill.kr` | Railway `echobill` 서비스 | CNAME → Railway 주소, **Proxied** (10/3) | 처음 연결할 때는 **DNS only**로 둬야 Railway가 인증서를 발급한다(10/2). 발급 후 Proxied로 바꿨다. 확인용 TXT `_railway-verify.api`는 지우지 않는다 |
| `echobill.kr`, `www.echobill.kr` | Redirect Rule `echobill.kr → app` | 기존 A·CNAME, Proxied | **302** → `https://app.echobill.kr` + 경로·쿼리 유지. 확정되면 301로 |

- MX·SPF·DKIM(`cf2024-1._domainkey`) 레코드는 Cloudflare 이메일 라우팅용이다. 지우지 않는다.
- Pages 빌드 설정: 루트 `frontend` · `npm run build` · 출력 `dist` · `NODE_VERSION=22` · `VITE_API_BASE_URL=https://api.echobill.kr`. `main` 머지 시 운영 배포, PR마다 미리보기(PR 체크 "Cloudflare Pages")
- Pages는 Git 연결 방식으로 만들었다. CLI(direct upload)로 만든 프로젝트는 나중에 Git 연결로 바꿀 수 없다.

### 웹 방화벽 · SSL (Cloudflare, 2026-10-03)

- **SSL/TLS 모드는 Full로 고정**(Automatic 끔). Automatic이면 자동 검사에서 Full (strict)로 오를 수 있는데, Railway 문서상 strict는 Railway 인증서 갱신 때 연결이 끊긴다
- WAF Custom rule **"운영 API 문서 차단"**: `api`의 `/docs`·`/redoc`·`/openapi.json` → 403
- Rate limiting **"API 요청 제한(임시, D09 전)"**: `/api/` IP당 10초 30회 → 429 (10/3 검증). 사용자별 제한은 BE 몫(D09)
- **Bot Fight Mode·Under Attack은 켜지 않는다** — 카톡 인앱 웹뷰의 API 호출이 챌린지에 막힌다. 카카오 콜백 경로(`/auth/kakao/callback`)에도 챌린지·국가 차단을 걸지 않는다
- 한계: Railway 기본 주소로 직접 들어오면 Cloudflare를 우회한다 → 사용성 테스트 전 Transform Rule로 비밀 헤더(`X-Origin-Secret`)를 붙이고 BE 미들웨어가 없으면 거부(`/health` 예외) — BE

## 2. CORS

FE와 BE 도메인이 다르므로 필수. 보안 가이드 「엄격한 CORS」.

- [x] 운영 `CORS_ORIGINS`에는 **FE 운영 도메인만** (`https://app.echobill.kr`) — 10/2 preflight 확인: 우리 origin 허용·credentials true, 다른 origin 400
- [x] `*.pages.dev` 와일드카드 금지 — 다른 사람의 Pages 사이트도 허용된다
- [ ] PR 미리보기 URL을 허용할지 결정. 허용한다면 개발용 BE에만, 운영 BE에는 넣지 않는다 — 지금은 운영 BE에 넣지 않았다(미리보기에서는 API 호출이 실패하는 게 정상). 개발용 BE를 만들 때 정한다
- [ ] JWT는 **HttpOnly · Secure · SameSite=Lax 쿠키** (DL-12) → BE `allow_credentials=True`, 허용 origin은 `https://app.echobill.kr` 하나(와일드카드 불가), FE `withCredentials: true` — BE 코드는 기준대로(`backend/app/api/auth.py`), 실제 응답 쿠키는 ①b 로그인 연결 후 §8에서 확인
- [x] 쿠키 `Domain`은 지정하지 않는다(= `api.echobill.kr` 전용). `app`과 `api`는 같은 사이트라 1st-party로 동작 — 코드 확인(`auth.py` `set_cookie`에 Domain 없음)

## 3. Railway 리전

- [x] 한국에서 가장 가까운 아시아 리전으로 설정 — **Southeast Asia (Singapore)**, 서비스 Settings → **Scale → Regions** (Deploy 섹션이 아님)
- [x] **BE 서비스와 PostgreSQL을 같은 리전에** — 둘 다 Singapore, 각 1 replica
- Replica Limits(최대 사용 상한)는 기본 8 vCPU/8 GB에서 1 vCPU/1 GB로 낮추기를 권장했다. 실제 값은 대시보드에서 확인

## 4. 환경 분리

| 환경 | FE | BE | DB |
|---|---|---|---|
| 로컬 | Vite dev (`VITE_API_BASE_URL` 비움 → 프록시) | uvicorn | 로컬 |
| 개발(선택) | Pages 미리보기 | Railway 개발 서비스 | **개발용 DB** |
| 운영 | Pages 운영 | Railway 운영 서비스 | **운영용 DB** |

- [ ] Railway 환경 **2개(production / development)**, DB도 환경별 분리 — 테스트 데이터가 운영 장부·FIFO에 섞이지 않게 (DL-24) — 10/2 현재 **production만**. 개발 환경을 상시로 켜면 사용량이 약 2배라, 필요할 때 만든다 (BE)
- [x] **운영 `DATABASE_URL`은 Railway env에만.** 어떤 로컬 `.env`에도 복사하지 않는다 — 참조 변수 `${{Postgres.DATABASE_URL}}`로 연결(값을 복사하지 않음)
- [x] FE 빌드 환경변수 `VITE_API_BASE_URL`을 환경별로 설정 (빌드 시 주입되므로 바꾸면 재빌드) — Pages 운영 `https://api.echobill.kr` (운영 번들에 포함 확인)
- [ ] 로컬 기본값은 SQLite지만 운영은 PostgreSQL(JSONB). JSONB·Row Lock(`SELECT FOR UPDATE`)이 걸린 기능(FeedbackEvent, FIFO)은 **PostgreSQL로도 테스트**한다

## 5. BE 운영 환경변수 (Railway)

- [x] Railway 서비스 **Root Directory는 비워 둔다(레포 루트).** 루트 `Dockerfile`·`railway.json`이 `backend/`와 `configs/`를 함께 빌드한다. `backend`로 잡으면 프롬프트(`configs/`)가 빠져 모든 요청이 조용히 Rule 폴백된다
- Railway 프로젝트 `echobill`(PM 계정, Hobby) · 서비스 `echobill`(GitHub `toryhyeon80/echobill`, 브랜치 `main`) + `Postgres`(볼륨). Railway GitHub 앱 권한은 **echobill 저장소만** 허용
- Settings → Source → **Wait for CI**를 켜면 CI가 통과한 커밋만 배포된다(권장). Serverless는 끈다(깨어나는 지연이 "LLM 5초 → Rule" 기준·시연과 맞지 않음)
- 코드 기본값 `debug=True`라 **`DEBUG=false`를 빠뜨리면 운영이 디버그 모드로 뜬다**

| 변수 | 운영 값 기준 |
|---|---|
| `APP_ENV` | `production` |
| `DEBUG` | `false` |
| `JWT_SECRET` | 새로 생성한 **32자 이상** 난수 (예: `openssl rand -hex 32`). 기본값이거나 32자 미만이면 **서버가 시작하지 않는다** |
| `BUSINESS_NUMBER_KEY` | 사업자번호 암호화 키 (SPEC §5.4). `python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"`로 생성. **없거나 코드 기본값이면 서버가 시작하지 않는다.** 바꾸면 저장된 번호를 못 읽으니 한 번 정하면 유지 |
| `TAX_MOCK_MODE` | 생략(기본 `issue`). 시연에서 실패·지연 안내를 보일 때만 `fail` / `delay` |
| `VOICE_ANSWER_FIELDS` | DL-31 음성 되묻기 답을 켤 칸 (`credit,qty,total,counterparty` 중). **비우면 전부 끔(기본)** — 10/12 칸별 판정(해석 성공 90%↑·틀린 값 채움 0건)을 통과한 칸만 넣는다. 꺼진 칸은 400 `VOICE_ANSWER_DISABLED`(FE는 버튼·키패드) |
| `ORIGIN_SECRET` | Cloudflare 우회 차단 (PM #11). **비어 있으면 끔(기본)**. 켜는 순서는 아래 「Cloudflare 우회 차단 켜기」 — 반드시 Cloudflare 먼저 |
| `PARSE_LIMIT_PER_MINUTE` · `PARSE_LIMIT_PER_DAY` | 사용자별 텍스트 `/parse` 상한 (PM #14). **0 = 끔(기본)**. 추천안 20/분 · 200/일 (D09). 초과 시 429 `RATE_LIMITED` + `Retry-After` |
| `AUDIO_LIMIT_PER_MINUTE` · `AUDIO_LIMIT_PER_DAY` | 사용자별 음성 상한 — `/parse-audio`와 DL-31 음성 답 `/voice-answer`를 **합쳐서** 셈(둘 다 STT 1회), 텍스트와 따로 셈(음성은 음성 상한만). **0 = 끔(기본)**. 추천안 10/분 · 100/일. 「일」은 **지난 24시간(롤링)**이지 KST 달력 하루가 아님 |
| `SPEAK_LIMIT_PER_MINUTE` · `SPEAK_LIMIT_PER_DAY` | 사용자별 읽어 주기 `/voice/speak` 상한 (DL-31 10/26, #210) — 텍스트·음성 상한과 따로 셈, 실패·시간 초과 호출도 셈. **0 = 끔(기본)**. 운영 20/분 · 200/일 |
| `TTS_MODEL` · `TTS_VOICE` · `TTS_MAX_CHARS` · `TTS_TIMEOUT_SECONDS` · `TTS_INSTRUCTIONS_FILE` | 읽어 주기 음성. 보통 넣지 않는다(코드 기본값 `gpt-4o-mini-tts` · `marin` · 80자 · 8초 · `configs/prompts/tts_instructions_ko.txt`). 키는 `OPENAI_API_KEY` 그대로, OpenAI 프로젝트 허용 모델에 `gpt-4o-mini-tts`가 있어야 함(10/24 dev·prod 추가) |
| ~~`JWT_ALGORITHM`~~ | 넣지 않는다 — 코드에서 HS256 고정 (env 값은 무시) |
| `JWT_ACCESS_TTL_HOURS` | `24` (SPEC §5.5) |
| `AUTH_COOKIE_SECURE` | `true` — HttpOnly 쿠키를 HTTPS에서만 보냄 (DL-12). `false`는 로컬 http 개발에서만 |
| `KAKAO_REST_API_KEY` | 카카오 개발자 콘솔 REST API 키 |
| `KAKAO_CLIENT_SECRET` | 카카오 콘솔 클라이언트 시크릿 (콘솔에서 활성화돼 있어 **필수**) |
| `KAKAO_REDIRECT_URI` | 운영 FE 콜백 주소 (예: `https://app.echobill.kr/auth/kakao/callback`). 카카오 콘솔에 등록한 Redirect URI와 **글자 하나까지 같아야** 한다. 빠뜨리면 기본값 localhost로 남아 운영 로그인이 실패한다 |
| `DATABASE_URL` | Railway PostgreSQL 연결 문자열 |
| `CORS_ORIGINS` | §2 기준 |
| `OPENAI_API_KEY` 등 | DL-17 기준으로 BE가 주입. **로컬 `.env` 키를 재사용하지 않고 운영용(`echobill-prod`)을 따로 발급** (§9) |
| `LLM_PARSE_ENABLED` | **`false`** (10/2). 운영 로그인(①b)이 들어오면 운영 OpenAI 키와 함께 `true` (§9) |
| `KO_NUMERAL_NORMALIZE` · `LLM_POSTPROCESS` | `true` (DL-26, 코드 기본값도 true) |

10/2 첫 배포에 넣은 것: `APP_ENV` · `DEBUG` · `AUTH_COOKIE_SECURE` · `CORS_ORIGINS` · `KAKAO_REDIRECT_URI` · `DATABASE_URL`(참조) · `JWT_SECRET` · `LLM_PARSE_ENABLED` · `KO_NUMERAL_NORMALIZE` · `LLM_POSTPROCESS`. `JWT_ALGORITHM`·`JWT_ACCESS_TTL_HOURS`는 코드 기본값(HS256·24)과 같아 생략. **카카오 키 2개는 입력 여부를 대시보드에서 확인** (①b 전 필수). 클라이언트 시크릿은 카카오 콘솔 [앱] > [플랫폼 키] > [REST API 키] > [클라이언트 시크릿] — 어드민 키가 아니다. 재발급하면 기존 코드가 바로 무효가 되니 팀원 로컬 `.env`와 맞출 것

- [x] 카카오 개발자 콘솔에 운영 **Redirect URI**를 등록한다 (PM, 10/2 — §1). 미리보기·임시 배포 주소로 로그인을 시험하려면 그 주소도 따로 등록해야 한다
- [x] 카카오 클라이언트 시크릿 재발급 후 Railway `KAKAO_CLIENT_SECRET` 갱신 (10/2). 팀원 로컬 `.env`는 값을 직접 전달

- [x] 비밀값은 Railway env에만. 레포·노션·슬랙에 평문으로 남기지 않는다 — `JWT_SECRET`은 PM이 `openssl rand -hex 32`로 만들어 Railway에 직접 입력
- [ ] 로그에 키·사업자번호·원문 PII가 찍히지 않는지 확인 (CLAUDE.md §3②)

### Cloudflare 우회 차단 켜기 (PM #11, 사용성 테스트 전)

Railway 주소(`*.up.railway.app`)로 직접 오면 Cloudflare 방화벽·요청 제한을 건너뛴다. 백엔드는 `X-Origin-Secret` 헤더가 없거나 틀리면 403 `ORIGIN_FORBIDDEN` (`/health`는 예외 — Railway 헬스체크). **순서를 바꾸면 모든 요청이 403**이니 아래 순서대로, PM·BE가 같이:

1. 값 만들기: `openssl rand -hex 32` (레포·채팅에 남기지 않음)
2. **(PM) Cloudflare 먼저** — Rules → Transform Rules → Modify Request Header: `api.echobill.kr` 요청에 `X-Origin-Secret: <값>` **설정(Set)**
3. 확인: 아직 백엔드는 꺼져 있어 동작 변화 없음 (Cloudflare가 헤더만 붙이는 상태)
4. **(BE) Railway env `ORIGIN_SECRET=<같은 값>`** → 재배포
5. 확인:
   - `https://api.echobill.kr/health` → 200, 앱에서 로그인·장부 정상 (Cloudflare 경유)
   - `https://<서비스>.up.railway.app/api/v1/auth/me` → **403** (우회 차단), `/health` → 200
6. 되돌리기: Railway `ORIGIN_SECRET` 삭제 → 재배포 (Cloudflare 규칙은 그대로 둬도 무해)

## 6. DB 마이그레이션 — 자동 배포에서 가장 흔한 사고 지점

`main` 머지가 곧 배포이므로 스키마 변경 순서를 정해 둔다.

- [x] 마이그레이션 도구: **Alembic** (`backend/alembic.ini`, `backend/migrations/`) — BE 결정 2026-10-06
- [x] 실행 방식: **Railway 배포 전 명령** (`railway.json` `deploy.preDeployCommand: alembic upgrade head`) — 실패하면 새 버전이 올라가지 않고 이전 버전 유지. 안전망으로 `Dockerfile` CMD도 `alembic upgrade head && uvicorn …` (이미 최신이면 아무것도 안 함)
- [ ] 첫 배포 후 Railway 배포 로그에서 Pre-deploy 단계의 `Running upgrade -> 0001` 확인 (대시보드 Settings → Deploy → Pre-deploy Command에 반영됐는지도)
- JSON 컬럼은 PostgreSQL에서 **JSONB** (`app/models/base.py` `JSONType`), SQLite는 JSON
- 「development 먼저」: Railway 개발 환경 대신 **CI의 PostgreSQL 16**에서 전체 테스트 + `upgrade head`를 매 PR 검증
- 모델을 바꾸면 같은 PR에 리비전 추가 (`backend/`에서): `export APP_ENV=development DATABASE_URL=sqlite:///./tmp_gen.db` (APP_ENV가 없으면 JWT_SECRET 검사에서 멈춤) → `alembic upgrade head` (빈 DB를 최신으로) → `alembic revision --autogenerate -m "설명"` → 생성 파일 검토 → `tmp_gen.db` 삭제. 빠뜨리면 CI(`tests/test_migrations.py`)가 실패한다
- [ ] 컬럼 삭제·이름 변경은 **2단계**(새 컬럼 추가·코드 전환 → 다음 배포에서 삭제)로 — 배포 중 구버전 코드가 깨지지 않게
- [ ] 스키마 drop·Railway 재설정 전에는 커밋·백업 (CLAUDE.md §6)
- [ ] **DB 수동 백업** — Railway 자동 백업은 Pro 전용이라 Hobby에선 `railway connect --tunnel-only` + `pg_dump`로 직접 한다. 시점: 첫 마이그레이션 직후 · 스키마 변경 전 · 사용성 테스트 전후 · 최종 평가 전. 백업 파일은 레포 금지·비공개 보관(`~/echobill-private/backup/`, 권한 600)·**만든 날부터 30일 안에 삭제**(처리방침 v1.0 3번, PM 10/12). 운영 DB는 외부 접속(Public Access)을 켜지 않고 터널로만 백업: 창 1 `railway connect Postgres --tunnel-only` → 창 2 `railway run --service Postgres sh -c 'PGHOST=127.0.0.1 PGPORT=<터널 포트> pg_dump -Fc -f <파일>'`. 기록: 2026-10-12 0004 적용 전 백업(11/11까지 삭제)
- Postgres에 **Public Access를 추가하지 않는다** — 지금은 private(`postgres.railway.internal`)만 열려 있다

## 7. 비용

- [x] Railway 요금제 확인 — **Hobby $5/월**(사용량 $5 포함, 넘으면 차액), 2026-10-02 결제, **매월 2일 자동 갱신**. 예상 월 $5~10 (production만)
- [x] Railway 사용량 상한 — Compute hard limit **$15** · 이메일 알림 **$8**, Agent(Railway AI) **$0**(기능 차단). 상한은 청구 기간마다 초기화
- [ ] 월 상한 **10만 원**(LLM·STT·인프라 합산), 알림 50%·80% 설정 (DL-17, PM) — Railway는 위와 같이 설정, OpenAI 쪽 알림 확인 필요
- [ ] 개발용 개인 키는 **하드 리밋**, 운영 키는 알림만 — 운영 키는 아직 발급 전 (§9)
- [ ] **11/10 발표 후, 12/2 갱신 전에 Railway 해지 여부 결정** (PM)

## 8. 첫 배포 스모크

- [x] `https://api.echobill.kr/health` 응답 — 10/2 200 `{"status":"ok","env":"production"}`, 인증서 정상
- [x] 인앱 웹에서 API 호출 시 CORS 오류 없음 — preflight 기준 확인(§2). 실제 화면 호출은 ①b 후 다시 확인
- [ ] 카카오톡 인앱 브라우저(Android·iOS)에서 진입·녹음 권한 요청 확인 (기획서 §4.4 1주차 선행 검증)
- [ ] 운영 DB에 테스트 데이터가 들어가지 않았는지 확인 — 10/2 첫 배포 직후 빈 DB(운영은 dev-token이 없고 로그인 전이라 쓰기 경로 없음). ①b 이후 정기 확인
- [x] `POST /api/v1/auth/dev-token`이 **404**인지 확인 (운영에서 열려 있으면 누구나 아무 사용자로 로그인 — DL-21 4-5) — 10/2 404
- [x] 로그인 없이 보호된 API 호출 시 **401** — 10/2 `GET /api/v1/ledger` 401
- [ ] 카카오 로그인 후 응답 쿠키에 `HttpOnly`·`Secure`·`SameSite=Lax`가 붙는지 브라우저 개발자 도구로 확인 (DL-12)
- [ ] 운영에서 API 문서(`/docs`·`/openapi.json`·`/redoc`)를 끈다 — 10/2 공개 상태(200), 10/3부터 Cloudflare WAF가 403으로 막지만(§1) Railway 주소로는 열려 있다. `APP_ENV=production`일 때 `docs_url=None` 등 (BE). FE 타입 생성(`npm run api:types`)은 로컬 서버를 쓰므로 영향 없음

## 9. 운영 LLM 켜기 (①b 로그인이 들어올 때)

10/2 첫 배포는 운영 사용자가 없어 LLM을 끄고(`LLM_PARSE_ENABLED=false`) Rule 파서로 띄웠다. 운영 로그인이 생기면 아래를 한 번에 한다 (PM).

- [ ] OpenAI에서 **운영용 키를 새로 발급**(`echobill-prod`) — 로컬 `.env` 키 재사용 금지(사고 시 따로 폐기, 개발·측정 비용과 분리)
- [ ] Railway `OPENAI_API_KEY`에 직접 입력
- [ ] OpenAI 사용량 알림 (월 상한 10만 원, DL-17)
- [ ] `LLM_PARSE_ENABLED=true`로 바꾸고 재배포. 프롬프트는 현재 것(`LLM_SYSTEM_PROMPT_FILE` 비움), 정규화·후처리는 이미 true (DL-26)
