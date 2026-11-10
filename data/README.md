# data/

Seed · Golden Dev/Test 검수본.

## Golden

- 경로: [`golden/`](golden/) · 포맷·배치: [`golden/README.md`](golden/README.md)
- **합계:** Dev+Test **50~100** (기획서 §8.1, M6: 「최종만 100」아님). **100건 = Dev 60 / Test 40 (D03-1 확정 2026-09-28, Test는 PM 출제)**: [`D03_D04_결정안.md`](D03_D04_결정안.md) · 출제 계획: [`golden_60_출제설계표.md`](golden_60_출제설계표.md)
- **출제·검수:** PM · **채점 스크립트(Exact 등):** AI(김수경)
- Golden **Test**로 튜닝·Champion 판단 금지 · **Dev**만 개선 비교
- 현재: `golden_dev.jsonl` **60건** (v2.5) — 001–040 PM 검수 완료(2026-09-28), 041–060 PM 검수 완료(2026-09-29)

## Seed

- 규모 목표 200~500 (프롬프트·Rule·오류 분석) — 별도 배치
- 경로: [`seed/`](seed/) · `data.py`(정답 원천, 사람이 수정) → `python3 build.py` → `seed_v1.jsonl`·`seed_v1_review.md`
- 현재: `seed_v1` **60건** (seed-v1.1, AI 초안 → PM 검수 2026-10-02) — 프롬프트·후처리 비교 측정용. 정상 41 / 추가 확인 19
  - v1.1 (10/3): 040 "다음 달에 받기로"를 외상(true) → **되묻기(credit null)** 로 변경. 외상·결제 단어가 없으면 추정하지 않는다(pp-1.3 정책, D04-6). **10/2 Seed 측정 결과(v1.0 기준)와 숫자를 바로 비교하지 않는다** — v1.1로 다시 채점해서 비교
- 정답 규칙은 Golden과 같다 (`golden/rules.py` 공유). `build.py`가 Golden 문장 재사용·품목·금액 조합·거래처명·프롬프트 겹침을 막는다 (설계표 §5 R3~R5)
- 200건 이상 목표는 이 60건을 바탕으로 AI 담당이 합성·변형 증폭 (별도 파일, 표본 검수) — 기한 10/12
