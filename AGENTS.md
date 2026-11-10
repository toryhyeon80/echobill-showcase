# AGENTS.md — EchoBill 에이전트 진입점

> Codex 등 `AGENTS.md`를 읽는 도구용 **포인터 파일**이다. 규칙 원문은 [`CLAUDE.md`](CLAUDE.md) 한 곳에만 둔다.
> 여기에 규칙을 복사해 늘리지 말 것 — 바뀌면 `CLAUDE.md`를 고친다.

## 1. 작업 전에 읽기 (순서)

1. [`CLAUDE.md`](CLAUDE.md) — 행동 강령·불변 규칙·검증 명령 (**필수**)
2. [`SPEC_MASTER.md`](SPEC_MASTER.md) — MVP 범위·필드명
3. [`docs/DOC_CRITERIA_ALIGNMENT.md`](docs/DOC_CRITERIA_ALIGNMENT.md) §7 — 결정 로그(DL-xx)
4. 설계·전략 제안 시 [`docs/agent/NORTH_STAR.md`](docs/agent/NORTH_STAR.md) · UI 작업 시 [`docs/design/README.md`](docs/design/README.md)
5. Golden·채점 작업 시 [`data/D03_D04_결정안.md`](data/D03_D04_결정안.md) · [`data/golden/README.md`](data/golden/README.md)

**충돌 시:** 기획서 ⊃ 기준 정리본 ⊃ SPEC_MASTER ⊃ CLAUDE.md ⊃ API/DB/WBS.

## 2. 자주 어기는 규칙 (전체는 CLAUDE.md §4)

- candidate는 사용자 확정 전 `confirmed` 금지 · 수량 임의 1 금지 · 단가 `null` 허용
- 필드명은 DL-01 그대로 (`utterance_text`, `qty`, `credit`, `total_amount` …) — 새 이름 도입 금지
- 타인·삭제 데이터는 404 (DL-21) · 상태변경 POST는 `X-Idempotency-Key`
- 키·비밀번호 하드코딩 금지, `.env` 커밋 금지 · 로그에 PII·토큰 평문 금지
- Golden **Test**로 튜닝 금지 · Golden 문장을 프롬프트 few-shot에 넣지 않음
- `data/golden/golden_dev.jsonl`·`golden_dev_review.md`는 생성물 — `data.py` 수정 후 `build.py`로만 갱신
- 요청과 무관한 리팩터·전면 재작성 금지

## 3. 검증 (CI와 같은 명령 — 통과해야 커밋)

```bash
cd backend && pip install -r requirements.txt && pytest -q
cd frontend && npm ci && npm run lint && npm run build
cd data/golden && python build.py && git diff --exit-code -- golden_dev.jsonl golden_dev_review.md
```

## 4. Git

- `main` 직접 push 금지 → `feature/<역할>-<작업>` 브랜치 + PR, Reviewer 최승현(`toryhyeon80`)
- 커밋 메시지: `feat(backend): …` / `fix(frontend): …` / `docs: …` (한국어 본문 가능)

## 5. 역할 → 경로

| 역할 | 주 경로 |
|---|---|
| PM 최승현 | `SPEC_MASTER.md` · `docs/` · Golden 검수 |
| AI 김수경 | `backend` 파서 · `configs/` · `data/` · `eval/` |
| Backend 최병철 | `backend/` |
| Frontend 이예령 | `frontend/` |

역할 밖 큰 변경은 PR 본문에 명시한다. 상세: [`docs/TEAM_ROLES.md`](docs/TEAM_ROLES.md)
