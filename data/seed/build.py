# -*- coding: utf-8 -*-
"""seed 생성기 — data.py → jsonl + 검수용 마크다운 + 자동 검산 (공통 규칙: ../golden/rules.py)

Golden과 같은 정답 검산에 더해, Seed가 Golden·프롬프트와 섞이지 않는지 검사한다 (설계표 §5 R3~R5).
"""
import difflib, importlib.util, io, json, re, sys
from pathlib import Path

from data import ROWS, REVIEW, COND, SEED_COUNTERPARTY_POOL

HERE = Path(__file__).resolve().parent
GOLDEN_DIR = HERE.parent / "golden"
PROMPT_DIR = HERE.parent.parent / "configs" / "prompts"
sys.path.append(str(GOLDEN_DIR))
from rules import RowSet, check_cond, check_rows  # noqa: E402

VERSION = "seed-v1.1"  # seed-v1.1: 040 정답을 되묻기(credit null)로 (pp-1.3 정책, 2026-10-03) · seed-v1.0: 1차 60건 (2026-10-02 PM 검수)
JSONL_OUT = "seed_v1.jsonl"
REVIEW_OUT = "seed_v1_review.md"
SIMILARITY_MAX = 0.75  # Golden 문장과 이 비율 이상 비슷하면 재사용으로 본다 (R5)
PROMPT_RUN = 6  # backend/tests/test_prompt_files.py와 같은 기준: 한글 6자 이상 연속 겹침 금지


def load_golden_data():
    """golden/data.py를 다른 모듈 이름으로 읽는다 (이 폴더의 data.py와 이름이 같아서)."""
    spec = importlib.util.spec_from_file_location("golden_data", GOLDEN_DIR / "data.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def compact(text):
    """비교용: 공백 제거."""
    return re.sub(r"\s+", "", text)


def hangul_runs(text, n=PROMPT_RUN):
    """한글만 남긴 문자열의 길이 n 연속 조각 집합."""
    h = re.sub(r"[^가-힣]", "", text)
    return {h[i:i + n] for i in range(len(h) - n + 1)}


def price_pairs(rows):
    """(품목명, 금액) · (품목명, 단가) 조합 — R4 비교용."""
    pairs = set()
    for r in rows:
        for nm, _qty, _unit, up, amt in r[3]:
            pairs |= {(nm, v) for v in (up, amt) if v is not None}
    return pairs


# ---------- 1. 자동 검산 ----------
golden = load_golden_data()
errors = check_rows(ROWS, REVIEW, COND) + check_cond(ROWS, COND)

# R3: 거래처명은 Seed 풀에서만, 풀은 Golden Dev·프롬프트 예시와 겹치지 않게
golden_names = {r[2] for r in golden.ROWS if r[2]} | set(golden.DEV_COUNTERPARTY_POOL)
prompt_text = "\n".join(p.read_text(encoding="utf-8") for p in sorted(PROMPT_DIR.glob("*.txt")))
for name in SEED_COUNTERPARTY_POOL:
    if name in golden_names:
        errors.append(f"SEED_COUNTERPARTY_POOL '{name}': Golden Dev 거래처명과 겹침")
    if name in prompt_text:
        errors.append(f"SEED_COUNTERPARTY_POOL '{name}': 프롬프트 파일에 나오는 이름")
for r in ROWS:
    if r[2] and r[2] not in SEED_COUNTERPARTY_POOL:
        errors.append(f"{r[0]}: 거래처명 '{r[2]}'은 SEED_COUNTERPARTY_POOL에 없음")

# R5: Golden 문장을 Seed로 재사용하지 않는다
golden_utts = [(r[0], compact(r[1])) for r in golden.ROWS]
for r in ROWS:
    mine = compact(r[1])
    for gid, gutt in golden_utts:
        ratio = difflib.SequenceMatcher(None, mine, gutt).ratio()
        if ratio >= SIMILARITY_MAX:
            errors.append(f"{r[0]}: {gid}와 문장 유사도 {ratio:.2f} ≥ {SIMILARITY_MAX}")

# R4: 품목명 + 금액(단가) 조합이 Golden과 같지 않게
shared = price_pairs(ROWS) & price_pairs(golden.ROWS)
for nm, v in sorted(shared, key=str):
    ids = [r[0] for r in ROWS if any(it[0] == nm and v in (it[3], it[4]) for it in r[3])]
    errors.append(f"{', '.join(ids)}: 품목·금액 조합 ({nm}, {v:,})이 Golden에도 있음 (R4)")

# 프롬프트 예시와 겹치면 프롬프트 효과를 Seed로 잴 수 없다 (test_prompt_files.py와 같은 기준)
prompt_runs = hangul_runs(prompt_text)
for r in ROWS:
    overlap = sorted(hangul_runs(r[1]) & prompt_runs)
    if overlap:
        errors.append(f"{r[0]}: 프롬프트와 한글 {PROMPT_RUN}자 이상 겹침 {overlap[:3]}")

if errors:
    print("검산 실패:")
    [print("  -", e) for e in errors]
    sys.exit(1)
print(f"검산 통과: {len(ROWS)}건")

seed = RowSet(ROWS, REVIEW, COND, split="Seed", version=VERSION)

# ---------- 2. JSONL ----------
with io.open(HERE / JSONL_OUT, "w", encoding="utf-8") as f:
    for r in seed.ordered():
        f.write(json.dumps(seed.rec(r), ensure_ascii=False) + "\n")

# ---------- 3. 검수용 마크다운 ----------
header = [
    f"# Seed 정답셋 검수용 — {VERSION}",
    "",
    f"> 원본(채점용): `{JSONL_OUT}` · 이 파일은 **사람 검수용**이며 `build.py`가 생성합니다 (직접 수정 금지).",
    "> 검수 결과는 이 파일이 아니라 `data.py`의 `REVIEW`에 적습니다. **검수 완료 건만** 측정에 씁니다.",
    "> Golden과 같은 정답 규칙(결정안 §0.4 G1~G9)을 따릅니다. 최종 Golden과 섞지 않습니다.",
    "",
]
with io.open(HERE / REVIEW_OUT, "w", encoding="utf-8") as f:
    f.write(seed.review_markdown(header))

# ---------- 4. 통계 ----------
seed.print_stats()
