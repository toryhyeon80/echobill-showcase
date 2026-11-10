# 에코빌(EchoBill) — 팀 역할 상세

> 기획서 §10의 4인 역할을 **실행 단위로 상세화**한 문서.  
> **범위·합격선·모델 본선**은 기획서 + [DOC_CRITERIA_ALIGNMENT.md](DOC_CRITERIA_ALIGNMENT.md)가 우선한다.  
> 요약 표: [SPEC_MASTER.md](../SPEC_MASTER.md) §2

---

## 팀장 [최승현] — 총괄 아키텍트 & PM

| | |
|---|---|
| GitHub | **`toryhyeon80`** |

### 프로젝트 및 인프라 통제
- 전체 스프린트 일정 관리
- GitHub **`main` 브랜치 머지 전권** · 코드 리뷰
- MLOps 파이프라인 관리 (프롬프트 버전·평가 루프·배포 게이트와 정합)

### E2E 핵심 아키텍처 설계
- React(FE) ↔ FastAPI(BE) ↔ LLM(AI) **데이터 흐름** 설계
- I/O 스키마 표준화 · 통신 API 골격 구축
- (팀 스킬 편차 시) BE/FE 골격·페어에 관여 가능. **범위 결정권은 PM만**

### 비즈니스 도메인 통제 (Data)
- “지저분한 음성·구어” → **장부 JSON** 정답 설계
- **Golden Set** 출제·검수 · 채점 기준 최종 확정  
  - 규모: 기획서 기준 **개발용+최종용 합계 50~100**. 「최종만 100」이 아님. 팀장 출제 목표도 이 상한 안에서 배분.

---

## 팀원 [김수경] — AI & 데이터 엔지니어

| | |
|---|---|
| GitHub | **`gyeong061`** |

### 하이브리드 LLM 추론 로직
- **MVP 본선 (기획·모델 가이드):** 클라우드 LLM 초기 **GPT-4o-mini (D01)** + JSON 스키마 · 5초 타임아웃  
- **폴백:** Rule(정규식 등) · Champion·수동 폼 정책은 SPEC §5.2  
- **선택 실험 (E2E·QA 후 여유 시):** Gemma 등 로컬 SLM · Claude 등 추가 모델 비교·체이닝 — WBS/상세안의 Gemma→Claude는 **본선 필수가 아님**

### 데이터 증폭 및 벤치마크
- 팀장이 검수한 Seed/Golden(합계 50~100 Golden)을 바탕으로 **합성·변형 증폭**
- Exact Match · 필수 필드 F1 · 거래 전체 정확도 채점 스크립트
- 피드백 개선 **전후 비교 ≥1회** (Dev Golden만)

### 환각 제어 및 Fallback 엔진
- 숫자·포맷 오류 방어용 **Rule 안전망**
- 출력 무결성: Pydantic·산술 검증·400 보완 연동  
  - 「출력 100% 무결성」은 **목표 표현**이지, 배포 합격선 95%와 혼동하지 않음 (Stretch≠Must)

---

## 팀원 [최병철] — 백엔드 & 인프라

| | |
|---|---|
| GitHub | **`choi0310`** |

### API 엔드포인트 구현
- 총괄 아키텍트 명세 기반 **Python FastAPI** REST
- 사용자·장부·입금·발행 관련 **DB 매핑·CRUD** (SQLAlchemy · PostgreSQL)
- Idempotency · JWT 소유권 · Sanitization · FIFO Row Lock

### 카카오 채널 연동
- **MVP 필수:** 인앱 웹 진입에 필요한 **인증(카카오 ID 검증→JWT)** · (필요 시) 채널 **링크/메뉴** 지원  
- **선택:** 챗봇 발화 수신 웹훅 → 내부 parse → 카드 응답 ([기준 정리본](DOC_CRITERIA_ALIGNMENT.md) §1.4). 웹훅을 MVP 블로커로 두지 않음

### 데이터 적재 및 외부 API
- AI 파이프라인 JSON → DB **안전 적재** (`candidate` / `confirmed`)
- **팝빌 규격 참고 Mock** 발행·상태 연동 (실 테스트망은 여유 시)
- Railway 등 BE/DB 배포 기반

---

## 팀원 [이예령] — 프론트엔드 UI/UX

| | |
|---|---|
| GitHub | **`hayan0308`** |

### 모바일 인앱 웹뷰 퍼블리싱
- 카톡 환경 · **한 손** 조작 · 카드형 **장부 확인/수정** UI
- 총괄이 깐 React(Vite) 구조 위 구현 · 노션 **v0 프롬프트** 활용 가능
- Safe Area · Skeleton · Empty State · 후보 local/sessionStorage 복구

### 운영 웹 콘솔 (선택·범위 주의)
- PC/태블릿용 **조회·대시보드** UI (미수·거래 한눈)  
- Soft Delete·임의 보정 UI는 **넣지 않음** (CS는 스크립트). 콘솔을 안 만들어도 기획 Must 충족 가능

### 클라이언트 상태 관리
- React + **Zustand** 등  
- 수량·단가 수정 시 **합계 즉시 재계산** 렌더링  
- 파일 분리 import · ~300줄 소프트 가이드 ([CLAUDE.md](../CLAUDE.md))

---

## 협업 한 줄

| | |
|---|---|
| 스키마·API 변경 | PM 승인 (D04/D09) |
| PR Reviewer | 최승현 · `main` 머지는 PM만 |
| 문서 충돌 시 | 기획서 → 기준 정리본 → SPEC → 본 역할서 |

---

## 변경 이력

| 날짜 | 내용 |
|---|---|
| 2026-09-28 | GitHub 계정: 최승현 `toryhyeon80` · 김수경 `gyeong061` · 이예령 `hayan0308` |
| 2026-09-23 | Backend 최병철 GitHub: `choi0310` |
| 2026-09-22 | 팀 상세 역할(이미지) 반영 · 기획 SSOT와 모델/웹훅/Golden/Admin 정합 주석 |
