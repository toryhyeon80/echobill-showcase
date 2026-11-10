# -*- coding: utf-8 -*-
"""golden_dev 생성기 — data.py → jsonl + 검수용 마크다운 + 자동 검산 (공통 규칙: rules.py)"""
import json, io, sys
from data import ROWS, REVIEW, COND, DEV_COUNTERPARTY_POOL
from rules import RowSet, check_cond, check_rows

VERSION = "v2.5"  # v2.5: 신규 20건 재작성(041~060, PM 검수 대기) · v2.4: 신규 20건 초안 · v2.3: expected_error_code (D03-5) · v2.2: 난이도 4조건 (D03-4) · v2.1: API 명세 키 (DL-01·02)

JSONL_OUT = "golden_dev.jsonl"
REVIEW_OUT = "golden_dev_review.md"

# ---------- 1. 자동 검산 ----------
errors = check_rows(ROWS, REVIEW, COND)
# 개발용 거래처명 풀 (설계표 §5 R3): 신규(41번~)는 기존 40건 거래처명 또는 풀에서만
legacy_cp = {r[2] for r in ROWS if r[2] and int(r[0].split("-")[-1]) <= 40}
for r in ROWS:
    if int(r[0].split("-")[-1]) > 40 and r[2] and r[2] not in legacy_cp | set(DEV_COUNTERPARTY_POOL):
        errors.append(f"{r[0]}: 거래처명 '{r[2]}'은 개발용 풀(DEV_COUNTERPARTY_POOL)에 없음 — 최종 평가용과 겹칠 수 있음")
errors += check_cond(ROWS, COND)
if errors:
    print("검산 실패:")
    [print("  -", e) for e in errors]
    sys.exit(1)
print(f"검산 통과: {len(ROWS)}건")

dev = RowSet(ROWS, REVIEW, COND, split="개발용 Golden", version=VERSION)

# ---------- 2. JSONL ----------
with io.open(JSONL_OUT, "w", encoding="utf-8") as f:
    for r in dev.ordered():
        f.write(json.dumps(dev.rec(r), ensure_ascii=False) + "\n")

# ---------- 3. 검수용 마크다운 ----------
header = [
    f"# Golden Dev 정답셋 검수용 — {VERSION} (수정본)",
    "",
    f"> 원본(채점용): `{JSONL_OUT}` · 이 파일은 **사람 검수용**이며 `build.py`가 생성합니다 (직접 수정 금지).",
    "> batch1(v1.0) 대비 변경된 건에는 **🔺 변경** 표시와 변경 사유가 붙어 있습니다.",
    "> 기획서 8.1에 따라 **검수 완료 건만 Golden에 편입**합니다. 검수 결과는 이 파일이 아니라 `data.py`의 `REVIEW`에 적습니다.",
    "",
]
with io.open(REVIEW_OUT, "w", encoding="utf-8") as f:
    f.write(dev.review_markdown(header))

# ---------- 4. 통계 ----------
dev.print_stats()
