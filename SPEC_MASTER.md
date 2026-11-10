# EchoBill (에코빌) — SPEC_MASTER

> **한 줄:** 말 한마디로 장부까지 — 카카오 인앱 웹에서 음성·텍스트 → AI 후보 → 사용자 확정 → 장부·미수·Mock 세금계산서.  
> **기간:** 2026-09-22 ~ 11-09 (7주) · 최종 발표 11-10 · **개발 동결 10-30** (§9, DL-18)  
> **SSOT:** 에코빌 프로젝트 기획서  
> **충돌 해소:** [docs/DOC_CRITERIA_ALIGNMENT.md](docs/DOC_CRITERIA_ALIGNMENT.md)  
> **설계 북극성:** [docs/agent/NORTH_STAR.md](docs/agent/NORTH_STAR.md)  
> **에이전트 규칙:** [CLAUDE.md](CLAUDE.md)

하위 문서(API·DB·WBS 등)와 본 SPEC이 충돌하면 **기획서 → 기준 정리본 → 본 SPEC** 순으로 따른다.

---

## 1. 제품 요약

| 항목 | 내용 |
|---|---|
| 메시지 | 말 한마디로 장부까지 (확정에는 **사용자 확인** 필요) |
| 고객 | 거래·정산을 직접 하는 **1인** 도소매·납품 사업자 |
| 진입 | 카카오 채널 **링크** → React **인앱 웹** |
| 핵심 흐름 | 입력 → AI 후보(`candidate`) → 확인·수정·거절 → `confirmed` → 장부·미수·캘린더 → (요청 시) Mock 발행 |
| 안전망 | 확인 전 장부 미반영 · LLM≤5초→Rule · Golden 80% Champion · 둘 다 실패 시 수동 폼 |

### 하지 않는다 (7주 MVP)

- 스토어 네이티브 앱 · 직원/다중 계정 · Admin Soft Delete UI · 확정 거래 사용자 삭제 UI
- 채팅봇 발화→자동 파싱→카드 (채널은 **진입만**, 웹훅 파싱은 **선택**)
- 팝빌 **실**테스트망 필수화 · 국세청 실전송 표현
- 로컬 Gemma→Claude 체이닝 필수화 · RAG · 대규모 FT · 오류 TTS(장애·네트워크 오류 읽어 주기. 음성 인식 실패·되묻기·확인 요약 읽어 주기는 DL-31 10/26 변경으로 포함)
- 입금 취소 UI · 기존 잔액 이월 · 결제/구독 · 알림톡 확인 UX

---

## 2. 팀 (4명)

| 역할 | 담당 | 핵심 |
|---|---|---|
| PM·아키텍트 | 최승현 (`toryhyeon80`) | 일정·main 머지·리뷰·E2E 아키·스키마·Golden 검수 |
| AI·데이터 | 김수경 (`gyeong061`) | 클라우드 LLM+Rule · 채점·증폭 · (선택) 로컬/Claude 실험 |
| Backend | 최병철 (`choi0310`) | FastAPI·DB·Auth·FIFO·Mock · (선택) 카톡 웹훅 |
| Frontend | 이예령 (`hayan0308`) | 인앱 3탭·Zustand · (선택) 운영 조회 콘솔 |

상세 R&R: [docs/TEAM_ROLES.md](docs/TEAM_ROLES.md)  
PM이 BE/FE 골격에 관여해도 됨. **범위 결정은 PM만.**

---

## 3. 스택 (동결)

| 층 | 선택 |
|---|---|
| FE | React · Vite · Zustand · Axios 또는 TanStack Query |
| BE | FastAPI · Pydantic · SQLAlchemy |
| DB | PostgreSQL (JSONB) |
| 배포 | FE: Cloudflare Pages · BE/DB: Railway · `main` 머지 시 자동 배포 — 체크리스트: [docs/deploy.md](docs/deploy.md) |
| AI 초기 | STT: 클라우드 1종(D01) · LLM: **GPT-4o-mini**(D01 실측 후 확정) · Rule: regex |
| Secrets | `.env` / 호스팅 env · 하드코딩 금지 |

로컬 SLM·GCP GPU: **선택**. 클라우드 LLM만이면 GCP 필수 아님.

---

## 4. 화면 IA (인앱 웹)

```
카카오 채널 링크 → JWT 발급 → 스플래시
├── Tab1 /record          음성·텍스트 입력
│     └── /record/confirm 후보 인라인 수정 · 확정
├── Tab2 /ledger          캘린더·미수 · FIFO 입금 모달
└── Tab3 /tax-invoice     Mock 발행 · 사업자 1회 등록
```

UX 원칙: 한 손 · 터치 ≥48px · Safe Area · 확인·원클릭 · 스켈레톤 · Empty State · 후보 `localStorage` 복구 · 라이트·고대비.  
**v0 프롬프트:** 노션 디자인 페이지 (레포에 전문 복제하지 않음). `docs/design/`에는 링크만.

운영 콘솔: MVP **선택**(조회·지표·피드백 추출). Soft Delete UI **없음**.

---

## 5. 도메인 규칙 (확정)

### 5.1 입력·파싱

- 1회: 거래처 **1곳** · 품목 **최대 3** · 초과·다중 거래처 → **400**, 부분 저장 금지
- `unit_price` **null 허용** (곱셈만 생략) · 수량 임의 `1` 금지
- 모든 품목 금액 있으면 합계↔총액 대조 · 불일치 400
- AI 결과 초기 `status=candidate` · [확정] 후에만 `confirmed`
- Sanitization: 마크다운·이모지·strip → Pydantic

### 5.2 Champion (D02 정책)

- LLM 호출 대기 **최대 5초** → Rule (STT·전체 RTT 제외)
- Rule 성공 → 클라이언트 **HTTP 200** (타임아웃은 내부 로그)
- 개선 비교용 Golden 정상 사례 기준 거래 전체 정확도 **&lt;80%** 또는 Rule 대비 열위 → 전체 Rule
- LLM·Rule 모두 &lt;80% → **구조화 수동 폼** (자동 파싱 중단)
- LLM 복귀: 동일 Dev Golden ≥80% + Rule 우위 + **PM 승인** (자동 복귀 없음)
- 최종 Golden: 선택·전환·복귀 판단 **금지** (최종 보고만)
- **95%** = Stretch only

### 5.3 미수·FIFO

- 캘린더: 등록일(`created_at`을 **KST로 변환한 날짜**, 저장은 UTC)별 **확정 외상 발생액** (입금해도 발생액 유지)
- 미수: 별도 잔액 조회 · 입금은 거래처 단위 · `created_at`↑ + PK↑ FIFO · **배분은 저장하지 않고 조회 시 재계산** (DL-22)
- 후보는 파싱 성공 시 서버 저장, **생성 7일 후 확정 차단** (DL-22)
- 초과 입금 400 · Row Lock(`SELECT FOR UPDATE`)
- Soft Delete(`deleted_at`) 건은 집계·FIFO·발행 대상 제외
- 입금·Mock 연결 건: CS Soft Delete **불가**

### 5.4 Mock 세금계산서

- Popbill 필드 참고 **자체 Mock** · UI에 모의 명시
- 상태: `requested` | `issued`(모의 완료) | `failed`
- 사업자번호 없으면 1회 모달 · 암호화 저장

### 5.5 Auth·보안

- 카카오 Provider ID → 서버 검증 → JWT (Access **24h**, Refresh Phase2)
- 모든 변경 API: `Authorization` + `X-Idempotency-Key` — 같은 키 재요청은 최초 응답 반환(24h), 다른 본문은 409 (DL-22)
- 소유권: 조회 쿼리에 JWT `user_id` 조건을 넣는다 · 토큰 없음·만료 401 · **타인 데이터는 404** (존재 여부 비노출, DL-21) · 서버 로그엔 소유권 불일치 기록
- 원음: STT 후 **즉시 삭제** · secrets `.env` · 로그 PII 마스킹 · rate limit (수치는 D09)

### 5.6 CS

- 채널 접수 → 본인·소유권 → PM 승인 → BE **스크립트** Soft Delete + `audit_log`
- 사용자/Admin 삭제 UI 없음

---

## 6. 데이터 모델 (요약)

| 엔티티 | 핵심 |
|---|---|
| Merchant | `user_id` PK · business_* |
| LedgerEntry | candidate/confirmed/rejected · items[] · total_amount · credit · **deleted_at** · tax_status(캐시) · idempotency · AI 메타 |
| FeedbackEvent | confirm/correct/reject · corrected_data(JSON Diff) |
| PaymentRecord | counterparty · amount · valid/canceled (entry 1:1 아님) |
| TaxInvoiceEvent | nts_state · fail_reason · idempotency · **원천 발행 상태** |
| audit_log | CS Soft Delete 전후 |

필수 평가 필드 5종: 거래처 · 품목명 · 수량 · 거래 총액 · 외상 여부.  
선택: 단위·단가·품목별 금액 (누락 시 null, AI 추정 금지).

---

## 7. API (요약)

공통: `Authorization: Bearer` · 상태변경 시 `X-Idempotency-Key` · JSON + Sanitization

| Method | Path | 비고 |
|---|---|---|
| POST | `/api/v1/auth/...` | 카카오 검증→JWT (D07에서 경로 확정) |
| POST | `/api/v1/ledger/parse` | `utterance_text` → candidate (필드명: 결정 로그 DL-01) |
| POST | `/api/v1/ledger/{id}/feedback` | confirm/correct/reject |
| GET | `/api/v1/ledger` | 목록·요약 |
| GET | `/api/v1/ledger/counterparty/{name}` | 미수 상세 |
| GET | `/api/v1/dashboard/unpaid` | 거래처별 미수 |
| POST | `/api/v1/payments` (또는 동등) | FIFO 입금 — 상세 경로 D09 |
| POST | `/api/v1/tax/issue` | Mock |
| POST | `/api/v1/webhook/kakao` | **선택** |

에러: 400 입력/검증 · 503 STT 등 복구실패 (DL-33) · 500 서버 · Rule 폴백 성공은 **200**.

---

## 8. 데이터셋·평가

| Set | 규모 | 용도 |
|---|---|---|
| Seed | 200~500 | 프롬프트·Rule·오류 분석 |
| Golden Dev+Test | **합계** 50~100 | Dev=개선·Champion / Test=최종 보고만 |
| Feedback | 운영 중 | 동의+가명 후 Seed 보강 |

- 사람 100% 검수만 Golden · 기대 결과 라벨: 정상 구조화 / 추가 확인 필요
- 최종 평가용만 난이도 6:4 · 파생 그룹 단위 누출 방지
- 지표: 거래 전체 정확도 · 금액 Exact · 필수 필드 F1 · 추가 확인 적합성(별도)
- 피드백 개선 **전후 비교 ≥1회** 필수

---

## 9. 7주 일정 (기획 기준 + 외부 마일스톤, DL-18)

### 9.1 외부 마일스톤 (365챌린지 — 기획서보다 우선)

| 날짜 | 제출·행사 | 준비 완료 시점 |
|---|---|---|
| **10/16 (금)** | 중간 보고서 (데모 영상·시연 빌드 없음, 10/10 조정) | 10/15 팀 내부 테스트 |
| **11/5 (목) 17:00** | 최종 보고서 | 11/2~3 최종 Golden 평가·사용성 결과 확보, 11/4 작성 |
| **11/6 (금) 17:00** | 최종 리허설 발표자료 | 11/5 데모 시나리오 확정 |
| 11/9 (월) | 리허설 피드백 반영 → 발표자료·보고서 수정 | |
| 11/10 (화) | **최종 발표회** | |

**개발 동결: 10/30 (금).** 11/2~3 최종 Golden 평가 이후에는 평가 결과에 영향을 주는 변경(파서·프롬프트·검증 규칙) 금지 — 보고서 수치와 시스템이 달라진다 (기획서 §8.1).

### 9.2 주차별 계획

| 주 | 기간 | 핵심 |
|:-:|---|---|
| 1 | 9/22~9/28 | 기반·D선행(D01·D04·D05·D07·D12) · 인앱 녹음 확인 (추석 9/24~25 제외) |
| 2 | 9/29~10/5 | STT·LLM 연동·Seed/Golden·기준선 |
| 3 | 10/6~10/12 | 확정·피드백·Rule 전환·CS 스크립트 · **모델 비교 1회(DL-11) 완료 → 중간 보고서 재료** |
| 4 | 10/12~10/18 | 10/14 운영 AI 켜기 · 10/15 팀 내부 테스트 · **10/16 중간 보고서** · 새 화면 연결 |
| 5 | 10/20~10/26 | 음성 E2E·Mock · **사용성 모집 주초 시작**(D11) · STT 교체 결정(DL-16) |
| 5 | 10/19~10/25 | 사장님 모집(지인·커뮤니티 10/19~, 방문 날짜는 10/27~31 중 약속 · 부족하면 테스트 주 현장 섭외 병행, 10/11 PM) · **10/23 베타 준비·AI 개선 마감** · 10/24~26 리허설·최종 측정 |
| 6 | 10/26~11/1 | **사용성 테스트 10/27~31** (AI 변경 금지) · **10/30 개발 동결** · 보고서 초안 |
| 7 | 11/2~11/9 | **11/2~3 최종 Golden 평가** · 11/3 D10 공개 범위 결정 · 11/4 보고서·리허설 자료 → **11/5 보고서 제출** · **11/6 리허설 발표자료 제출** · 평가 무관 버그 수정만 · 11/9 수정 |
| — | 11/10 | 최종 발표회 |

상세 태스크: WBS는 **실행 참고** (WBS 4단계 11/4~11/9 「QA·버그 픽스」도 위 동결 규칙을 따른다). 모델·95%·팝빌·웹훅은 [DOC_CRITERIA_ALIGNMENT](docs/DOC_CRITERIA_ALIGNMENT.md) §1.

---

## 10. 완료 기준 (Must)

- [ ] 입력→후보→확인/수정/거절→확정 · 확인 전 미반영
- [ ] 3품목 초과·다중 거래처·산술 모순 400 · 단가 null
- [ ] Idempotency · 소유권 격리
- [ ] FIFO·초과 입금 차단 · Soft Delete 집계 제외·연결건 Lock
- [ ] Mock 3상태 · 모의 표시
- [ ] LLM 5초→Rule→200 · Champion/수동폼 정책
- [ ] Dev Golden 비교 + 피드백 개선 1회 · 최종 Golden 격리 보고
- [ ] 배포본 · 제한사항 · 데모 리허설

95% 미달·음성 불안정(텍스트 전환)·팝빌 실연동 미실시는 **Must 실패 사유 아님**.

---

## 11. 열린 결정 (D01~D12)

구현·수집 전 PM 확정. 목록·담당: 기획서 14.2 및 [DOC_CRITERIA_ALIGNMENT](docs/DOC_CRITERIA_ALIGNMENT.md) §3.

결정 시: 일자·값·영향 API/DB를 본 SPEC 또는 노션에 기록하고 하위 문서 갱신.

---

## 12. 레포 배치 (예정)

```text
echobill/
├── SPEC_MASTER.md          ← 본 파일
├── CLAUDE.md
├── README.md
├── docs/
│   ├── DOC_CRITERIA_ALIGNMENT.md
│   └── design/README.md    ← 노션·v0 링크
├── backend/                ← FastAPI (신규)
├── frontend/               ← React+Vite (신규)
└── data/                   ← Seed·Golden (검수본)
```

`voiceledger`는 참고만. 코드 이식 없음.
