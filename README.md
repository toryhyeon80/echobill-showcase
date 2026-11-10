# EchoBill (에코빌)

> **제출용 공개 사본** — 아이펠(모두의연구소) 5조 「말로해!」 최종 결과물. 개발 저장소(비공개)의 `main` 2026-11-03 시점 코드를 커밋 기록 없이 옮겼습니다. 평가용 음성 판정 세트와 내부 노션 링크가 든 QA 기록 일부는 제외했습니다.

카카오톡 인앱 웹 기반 1인 사업자 AI 장부 · **9/22~11/9** (최종 발표 11/10, 개발 동결 10/30 — [SPEC §9](SPEC_MASTER.md)).

> 말 한마디로 장부까지 — 단, **사용자 확정 전** 장부 미반영 (`candidate` only).

## 문서 (이 순서로 읽기)

1. [SPEC_MASTER.md](SPEC_MASTER.md) — 통합 스펙  
2. [CLAUDE.md](CLAUDE.md) — 코딩·에이전트 규칙  
3. [docs/agent/NORTH_STAR.md](docs/agent/NORTH_STAR.md) — 설계 북극성  
4. [docs/TEAM_ROLES.md](docs/TEAM_ROLES.md) — 4인 역할  
5. [docs/DOC_CRITERIA_ALIGNMENT.md](docs/DOC_CRITERIA_ALIGNMENT.md) — 문서 간 기준  
6. [docs/design/README.md](docs/design/README.md) — v0·노션 링크  

제품 SSOT는 **에코빌 프로젝트 기획서**. `voiceledger` 코드 이식 없음.

---

## 로컬 실행

### 사전 요구

- Python 3.11+ (권장 3.12 · `python3.12 -m venv .venv`)
- Node.js 20+
- (선택) PostgreSQL — 없으면 SQLite로 BE 스모크 가능

### 1) Backend (FastAPI)

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # JWT_SECRET 등 로컬 값으로 변경

uvicorn app.main:app --reload --port 8000
```

스모크:

- `GET http://127.0.0.1:8000/health`
- `POST /api/v1/auth/dev-token` → JWT
- `POST /api/v1/ledger/parse` + `Authorization: Bearer …` → **status=candidate** mock

테스트:

```bash
cd backend && pytest
```

### 2) Frontend (React + Vite)

```bash
cd frontend
npm install
cp .env.example .env.local   # 비워 두면 Vite proxy → :8000
npm run dev
```

브라우저: `http://127.0.0.1:5173`  
하단 3탭: `/record` · `/ledger` · `/tax-invoice`

빌드 검증:

```bash
cd frontend && npm run build
```

---

## 이번 스캐폴드 범위

| 포함 | 제외 (의도) |
|---|---|
| `/health`, JWT stub, parse mock candidate | 실카톡 웹훅 본선 |
| SQLAlchemy 모델 초안 + migration stub | D04/D09 스키마 freeze |
| 인앱 3탭 셸 · API 클라이언트 · Zustand | 완전한 FIFO/UI 폴리시 |
| Mock 세금 탭 카피 | 팝빌 실연동 · Gemma 체이닝 |

규칙 요약: `main` 직접 push 금지 · confirm 전 confirmed 금지 · 세금은 Mock · 웹훅은 선택.
