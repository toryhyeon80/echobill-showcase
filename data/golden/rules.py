# -*- coding: utf-8 -*-
"""Golden·Seed 공통 정답 규칙 — 검산, 레코드 생성, 검수용 마크다운.

각 세트의 build.py가 자기 data.py의 ROWS·REVIEW·COND를 넘겨 쓴다.
세트마다 다른 검사(거래처명 풀 등)는 각 build.py에 둔다.
"""
from collections import Counter

DIFF = {"C": "명확한 표현", "X": "복잡·생략 표현"}
OUT = {"OK": "정상 구조화 가능", "NC": "추가 확인 필요"}
COND_KEYS = ["word_order", "no_correction", "required_direct", "standard_unit_int"]
COND_LABELS = ["① 어순 [거래처→품목→수량→금액→결제]", "② 정정·군더더기 없음",
               "③ 필수 5종 직접 등장 (계산 도출 불필요)", "④ 표준 단위·정수 수량·심한 축약 없음"]
ERROR_CODE = {"필수값 누락": "MISSING_REQUIRED_FIELD", "산술 모순": "AMOUNT_MISMATCH",
              "금액 범위 위반": "AMOUNT_OUT_OF_RANGE"}
RANGE_CODE = {("counterparty",): "MULTIPLE_COUNTERPARTIES_DETECTED", ("items",): "EXCEED_MAX_ITEMS"}
REVIEW_STATES = {"검수 완료", "재검토"}
# 금액·수량 허용 범위 (D04-5 · 결정안 G3): 금액·단가 1원~1억 원, 수량 0 초과~100,000
AMOUNT_MIN, AMOUNT_MAX, QTY_MAX = 1, 100_000_000, 100_000


def error_code_of(ctype, req):
    """확인 유형 → API 기대 오류 코드 (D03-5). 입력 범위 위반은 보완 필드로 구분."""
    if ctype is None:
        return None
    if ctype == "입력 범위 위반":
        return RANGE_CODE.get(tuple(req))
    return ERROR_CODE.get(ctype)


def check_rows(rows, review, cond):
    """정답 산술·라벨·금액 범위·검수 기록·4조건 정합성을 검사해 오류 메시지 목록을 돌려준다."""
    errors = []
    for r in rows:
        rid, utt, cp, items, total, credit, diff, outcome, ctype, req, reject, grp, tsrc, memo, chg = r
        for i, (nm, qty, unit, up, amt) in enumerate(items):
            # 단가가 있으면 수량×단가 = 금액 (7.1)
            if up is not None and qty is not None and amt is not None and ctype != "산술 모순":
                if round(qty * up, 6) != amt:
                    errors.append(f"{rid} items[{i}] {qty}x{up} != {amt}")
            # 수량이 없으면 단가가 있어도 품목 금액을 계산할 수 없음 (E 유형).
            if up is not None and qty is not None and amt is None and ctype != "산술 모순":
                errors.append(f"{rid} items[{i}] 단가 있는데 금액 null")
        amts = [it[4] for it in items]
        if items and all(a is not None for a in amts) and total is not None:
            if ctype == "산술 모순":
                pass
            elif sum(amts) != total:
                errors.append(f"{rid} 품목 금액 합계 {sum(amts)} != 총액 {total}")
        # 라벨 정합성
        if outcome == "NC" and ctype is None:
            errors.append(f"{rid} 추가 확인 필요인데 confirm_type 없음")
        if outcome == "OK" and (ctype or req or reject):
            errors.append(f"{rid} 정상인데 확인 필드가 있음")
        if ctype is not None and error_code_of(ctype, req) is None:
            errors.append(f"{rid} 기대 오류 코드를 정할 수 없음 (입력 범위 위반은 보완 필드가 [\"counterparty\"] 또는 [\"items\"])")
        if ctype == "필수값 누락" and not req:
            errors.append(f"{rid} 필수값 누락인데 보완 필요 필드 없음")
        # 선택 필드 추정 금지: 단위가 있으면 발화에 그 글자가 있어야 함
        for i, (nm, qty, unit, up, amt) in enumerate(items):
            if unit and unit not in utt:
                errors.append(f"{rid} items[{i}] unit '{unit}' 이 발화에 없음")
    for r in rows:
        rid, utt, cp, items, total, credit, diff, outcome, ctype, req, reject, grp, tsrc, memo, chg = r
        out = []
        for i, (nm, qty, unit, up, amt) in enumerate(items):
            if qty is not None and not (0 < qty <= QTY_MAX):
                out.append(f"items[{i}].qty")
            for key, v in (("unit_price", up), ("amount", amt)):
                if v is not None and not (AMOUNT_MIN <= v <= AMOUNT_MAX):
                    out.append(f"items[{i}].{key}")
        if total is not None and not (AMOUNT_MIN <= total <= AMOUNT_MAX):
            out.append("total_amount")
        if ctype == "금액 범위 위반":
            if sorted(out) != sorted(req):
                errors.append(f"{rid} 금액 범위 위반 보완 필드 {req} != 범위 밖 필드 {out} (G3)")
        elif out:
            errors.append(f"{rid} 범위 밖 값 {out}인데 confirm_type이 '금액 범위 위반'이 아님")
    # 검수 기록 정합성
    ids = {r[0] for r in rows}
    for rid, (state, date, _memo) in review.items():
        if rid not in ids:
            errors.append(f"REVIEW {rid}: ROWS에 없는 ID")
        if state not in REVIEW_STATES:
            errors.append(f"REVIEW {rid}: 상태 '{state}'는 {sorted(REVIEW_STATES)} 중 하나여야 함")
        if not date:
            errors.append(f"REVIEW {rid}: 검수일 누락")
        if state == "검수 완료" and None in cond.get(rid, (None,) * 4):
            errors.append(f"REVIEW {rid}: 검수 완료인데 난이도 4조건(COND)이 모두 판정되지 않음")
    return errors


def check_cond(rows, cond):
    """난이도 4조건 기록이 ROWS에 있는 ID이고 (True/False/None) 4개 튜플인지 검사한다 (D03-4)."""
    errors = []
    ids = {r[0] for r in rows}
    for rid, conds in cond.items():
        if rid not in ids:
            errors.append(f"COND {rid}: ROWS에 없는 ID")
        if not (isinstance(conds, tuple) and len(conds) == 4 and all(c in (True, False, None) for c in conds)):
            errors.append(f"COND {rid}: (True/False/None) 4개 튜플이어야 함")
    return errors


class RowSet:
    """한 세트(ROWS·REVIEW·COND)의 검수 상태·난이도 산출·레코드·검수용 마크다운 생성."""

    def __init__(self, rows, review, cond, *, split, version):
        self.rows, self.review, self.cond = rows, review, cond
        self.split, self.version = split, version

    def ordered(self):
        """ID 숫자 순서로 정렬한 ROWS."""
        return sorted(self.rows, key=lambda r: int(r[0].split("-")[-1]))

    def review_of(self, rid, chg):
        """PM 검수 기록이 있으면 그 값, 없으면 change_log 유무로 미검수/재검토."""
        if rid in self.review:
            state, date, memo = self.review[rid]
            return state, date, memo
        return ("재검토" if chg else "미검수"), None, ""

    def cond_of(self, rid):
        """4조건 판정값. 기록이 없으면 모두 None(미판정)."""
        return self.cond.get(rid, (None,) * 4)

    def label_of(self, rid):
        """4조건 AND로 난이도 산출. 하나라도 None이면 산출하지 않는다 (D03-4)."""
        c = self.cond_of(rid)
        if None in c:
            return None
        return DIFF["C"] if all(c) else DIFF["X"]

    def rec(self, r):
        """ROWS 한 줄 → 채점용 JSONL 레코드."""
        rid, utt, cp, items, total, credit, diff, outcome, ctype, req, reject, grp, tsrc, memo, chg = r
        return {
            "id": rid,
            "utterance_text": utt,
            "expected_json": {
                "counterparty": cp,
                "items": [{"name": n, "qty": q, "unit": u,
                           "unit_price": p, "amount": a} for n, q, u, p, a in items],
                "total_amount": total,
                "credit": credit,
            },
            "difficulty_label": self.label_of(rid),
            "difficulty_conditions": dict(zip(COND_KEYS, self.cond_of(rid))),
            "difficulty_label_draft": DIFF[diff],
            "expected_outcome": OUT[outcome],
            "confirm_type": ctype,
            "expected_error_code": error_code_of(ctype, req),
            "required_clarifications": req,
            "reject_reason": reject,
            "review_status": self.review_of(rid, chg)[0],
            "reviewed_at": self.review_of(rid, chg)[1],
            "review_memo": self.review_of(rid, chg)[2],
            "split": self.split,
            "source": "합성·직접 작성",
            "group_id": grp,
            "version": self.version,
            "notes": {"total_source": tsrc, "memo": memo},
            "change_log": chg,
        }

    def review_markdown(self, header):
        """검수용 마크다운 본문. header는 맨 위에 둘 줄 목록(제목·안내문)."""
        order = self.ordered()
        L = list(header)
        A = L.append
        ch = [r for r in order if r[14]]
        nc = [r for r in order if r[7] == "NC"]
        st = Counter(self.review_of(r[0], r[14])[0] for r in order)
        A(f"총 **{len(self.rows)}**건 · 변경 **{len(ch)}**건 · 추가 확인 필요 **{len(nc)}**건"
          f"({len(nc)/len(self.rows)*100:.1f}%)")
        A("")
        A(f"검수 현황: 검수 완료 **{st['검수 완료']}** · 재검토 **{st['재검토']}** · 미검수 **{st['미검수']}**")
        A("")
        judged = [r for r in order if self.label_of(r[0])]
        mism = [r for r in judged if self.label_of(r[0]) != DIFF[r[6]]]
        A(f"난이도 4조건: 판정 **{len(judged)}** / {len(self.rows)}건 · 초안 라벨과 다름 **{len(mism)}**건")
        A("")
        A("## 변경 요약")
        A("")
        A("| ID | 변경 내용 |")
        A("|---|---|")
        for r in ch:
            A(f"| `{r[0]}` | " + "<br>".join(r[14]) + " |")
        A("")
        A("---")
        A("")

        for r in order:
            rid, utt, cp, items, total, credit, diff, outcome, ctype, req, reject, grp, tsrc, memo, chg = r
            mark = " · 🔺 변경" if chg else ""
            lab = self.label_of(rid)
            A(f"## {rid} · `{OUT[outcome]}` · {lab or '난이도 미판정'} · `{grp}`{mark}")
            A("")
            A(f"**발화:** {utt}")
            A("")
            pay = {None: "—", True: "외상", False: "현금성"}[credit]
            ts = {"stated": "발화 명시", "derived": "계산 도출", None: "—"}[tsrc]
            A(f"**거래처:** {cp or '— (불명)'} · **총액:** {won(total)} ({ts}) · **결제:** {pay}")
            A("")
            if items:
                A("| 품목 | 수량 | 단위 | 단가 | 금액 |")
                A("|---|---:|---|---:|---:|")
                for n, q, u, p, a in items:
                    A(f"| {n} | {num(q)} | {u or '—'} | {won(p)} | {won(a)} |")
                A("")
            else:
                A("_품목: 기록하지 않음 (아래 사유 참조)_")
                A("")
            if ctype:
                A(f"**확인 유형:** `{ctype}` → 기대 오류 코드 `{error_code_of(ctype, req)}`")
                if req:
                    A("")
                    A("**보완 필요 필드:** " + ", ".join(f"`{x}`" for x in req))
                if reject:
                    A("")
                    A(f"**거절 사유:** {reject}")
                A("")
            if memo:
                A(f"_메모: {memo}_")
                A("")
            if chg:
                A("**🔺 v1.0 대비 변경**")
                A("")
                for c in chg:
                    A(f"- {c}")
                A("")
            sym = {True: "✅", False: "❌", None: "⬜ 미판정"}
            head = f"**표현 난이도 4조건** (초안: {DIFF[diff]}"
            head += f" → 산출: **{lab}**" + (" ⚠️ 초안과 다름" if lab != DIFF[diff] else "") + ")" if lab else ")"
            A(head)
            A("")
            for c, label in zip(self.cond_of(rid), COND_LABELS):
                A(f"- {sym[c]} {label}")
            A("")
            state, date, rmemo = self.review_of(rid, chg)
            box = "x" if state == "검수 완료" else " "
            A(f"- [{box}] PM 검수: **{state}**" + (f" ({date})" if date else "") + (f" — {rmemo}" if rmemo else ""))
            A("")
            A("---")
            A("")
        return "\n".join(L)

    def print_stats(self):
        """세트 통계를 콘솔에 출력한다."""
        rows = self.rows
        st = Counter(self.review_of(r[0], r[14])[0] for r in rows)
        print("\n[난이도 초안]", dict(Counter(DIFF[r[6]] for r in rows)))
        print("[난이도 산출]", dict(Counter(self.label_of(r[0]) or "미판정" for r in rows)))
        print("[기대 결과]  ", dict(Counter(OUT[r[7]] for r in rows)))
        print("[검수 상태]  ", dict(st))
        print("[확인 유형]  ", dict(Counter(r[8] for r in rows if r[8])))
        print("[총액 출처]  ", dict(Counter(r[12] for r in rows)))
        print("[그룹]       ", len({r[11] for r in rows}), "개")
        print("[거래처]     ", len({r[2] for r in rows if r[2]}), "개")
        cross = Counter((DIFF[r[6]], OUT[r[7]]) for r in rows)
        print("[교차(초안)] ", {f"{a}/{b}": c for (a, b), c in cross.items()})


def won(v):
    """금액 표시 (없으면 —)."""
    return "—" if v is None else f"{v:,}"


def num(v):
    """수량 표시 (정수면 소수점 없이)."""
    if v is None:
        return "—"
    return str(int(v)) if float(v).is_integer() else str(v)
