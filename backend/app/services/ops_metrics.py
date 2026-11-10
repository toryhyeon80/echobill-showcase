"""Ops metrics (PM #18) — AI health from data already in the DB, plus error counts from logs.

Totals across all users; no utterance text, names or user IDs leave this module.
Feeds the week-6 "피드백 기반 개선 전후 비교". Filtering is done in Python on created_at
(KST days) so SQLite and PostgreSQL agree; MVP data volume is small.
"""

import math
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import FeedbackEvent, LedgerEntry
from app.services.demo_seed import demo_filter
from app.services.fifo import KST
from app.services.idempotency import as_utc
from app.services.moved_credit import MOVED

FIELDS = ("counterparty", "items", "total_amount", "credit")
# PM #23: post-process rule → what the user would correct if the rule was wrong
RULE_TARGET = {
    "credit_cleared_no_word": "credit",
    "credit_cleared_negated": "credit",
    "credit_cleared_contradiction": "credit",  # pp-1.4 (#82)
    "credit_cleared_partial_payment": "credit",
    "credit_cleared_not_received": "credit",
    "credit_from_text": "credit",
    "unit_price_to_amount": "items",
    "unit_price_cleared": "items",
    "pack_spec_qty_cleared": "items",  # PM 10/13
    "total_restored_stated": "total_amount",  # PM 10/12
    "item_amount_invented": "total_amount",  # PM 10/13
    "total_restored_spoken": "total_amount",  # PM 10/16
    "counterparty_is_item_cleared": "counterparty",  # PM 10/29
    # cascade (회의 10/23): the two models disagreed and the field was asked again — same labels as ledger._RULE_FIELDS (#213)
    "cascade_counterparty": "counterparty",
    "cascade_item_name": "items",
    "cascade_item_qty": "items",
    "cascade_total_amount": "total_amount",
    "cascade_credit": "credit",
}
ITEM_KEYS = ("name", "qty", "unit", "unit_price", "amount")
_PP_400 = re.compile(r"postprocess_400 version=(\S+) rules=(\S+) error_code=(\S+)")
_API_ERROR = re.compile(r"api_error method=(\S+) route=(\S+) status=(\d+) error_code=(\S+)")
_VOICE_ANSWER = re.compile(r"voice_answer field=(\S+) status=(ok|failed) reason=(\S+)")
VOICE_FIELDS = ("credit", "qty", "total", "counterparty")  # DL-31 칸 (app.services.voice_answer.FIELDS)


@dataclass
class PeriodMetrics:
    """One reporting window."""

    label: str
    candidates: int = 0
    llm_attempts: int = 0  # parser_type=llm or fell back from LLM
    llm_fallbacks: int = 0
    rule_only: int = 0  # LLM disabled (LLM_PARSE_ENABLED=false)
    manual: int = 0
    llm_p50_ms: int | None = None  # LLM attempts (incl. fallbacks) — the number to compare over time
    llm_p95_ms: int | None = None
    rule_p50_ms: int | None = None  # Rule only (LLM off) ≈ 0ms; kept apart so it cannot drag LLM numbers down
    rule_p95_ms: int | None = None
    decisions: int = 0  # confirm + correct on AI candidates (llm · rule) — the correction-rate denominator
    rejects: int = 0
    manual_decisions: int = 0  # decisions on 보완 제출(manual) candidates — user-typed, not an AI result
    # 정의 (수경님 10/7 + PM 10/8): 바꿈 = AI 값 → 다른 값, 지움 = AI 값 → 비움(품목 삭제 포함),
    # 채움 = AI null → 사용자 값. 바꿈·지움 = AI가 틀린 값을 냄(수정), 채움 = AI가 못 채움(보완)
    field_changes: Counter = field(default_factory=Counter)  # top-level field → decisions with 바꿈
    field_clears: Counter = field(default_factory=Counter)  # top-level field → decisions with 지움
    field_fills: Counter = field(default_factory=Counter)  # top-level field → decisions with 채움 (e.g. 외상 선택)
    # 수정률 numerator: decisions where the field had any 바꿈 or 지움 — counted once per decision,
    # so changing one item and deleting another in the same decision is one wrong decision, not two
    field_wrong: Counter = field(default_factory=Counter)
    item_key_changes: Counter = field(default_factory=Counter)  # item sub-field → decisions with 바꿈
    item_key_clears: Counter = field(default_factory=Counter)  # item sub-field → decisions with 지움
    item_key_fills: Counter = field(default_factory=Counter)  # item sub-field → decisions with 채움
    # PM #23 post-process rules: candidates it fired on · decided · user corrected its target · 외상/현금 picked
    rule_applied: Counter = field(default_factory=Counter)
    rule_decided: Counter = field(default_factory=Counter)
    rule_corrected: Counter = field(default_factory=Counter)
    rule_credit_after: Counter = field(default_factory=Counter)  # (rule, True|False) — what the user chose
    # repair_of: a rule made the parse ask again (400) and the user's repair submit was linked back
    rule_repaired: Counter = field(default_factory=Counter)
    rule_repair_credit: Counter = field(default_factory=Counter)  # (rule, True=미수금 | False=결제 완료)

    @property
    def fallback_rate(self) -> float | None:
        return self.llm_fallbacks / self.llm_attempts if self.llm_attempts else None

    def correction_rate(self, name: str) -> float | None:
        """수정률 = decisions where a value the AI filled was wrong: 바꿈 + 지움 (채움 not included)."""
        if not self.decisions:
            return None
        return self.field_wrong[name] / self.decisions


def percentile(values: list[int], pct: float) -> int | None:
    """Nearest-rank percentile (p95 of 20 values = the 19th smallest)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, math.ceil(pct / 100 * len(ordered)))
    return ordered[rank - 1]


def _kind(before: Any, after: Any) -> str | None:
    """"change" (AI value → other value), "clear" (AI value → removed), "fill" (AI null → user value)."""
    if before == after:
        return None
    if after is None:
        return "clear"
    return "fill" if before is None else "change"


def _item_kinds(diff: dict[str, Any]) -> dict[str, set[str]]:
    """{"change"|"clear"|"fill": item sub-fields} across items, compared by position.
    An added item counts as filled; a removed item counts as cleared for every key the AI had set."""
    kinds: dict[str, set[str]] = {"change": set(), "clear": set(), "fill": set()}
    items = diff.get("items")
    if not items:
        return kinds
    before, after = items.get("before") or [], items.get("after") or []
    for i in range(max(len(before), len(after))):
        b = before[i] if i < len(before) else {}
        a = after[i] if i < len(after) else {}
        for key in ITEM_KEYS:
            kind = _kind(b.get(key), a.get(key))
            if kind:
                kinds[kind].add(key)
    return kinds


def _in_period(value: datetime, start: date, end: date) -> bool:
    day = as_utc(value).astimezone(KST).date()
    return start <= day <= end


def compute(db: Session, label: str, start: date, end: date) -> PeriodMetrics:
    """Metrics for KST days start..end (inclusive). Includes soft-deleted rows — they were real traffic."""
    m = PeriodMetrics(label=label)
    llm_times: list[int] = []
    rule_times: list[int] = []
    for e in db.scalars(select(LedgerEntry).where(demo_filter())):  # demo account rows are not traffic
        if not _in_period(e.created_at, start, end):
            continue
        if e.parser_type == MOVED:  # typed totals with an approximate date, not parsed traffic (병철 #214)
            continue
        m.candidates += 1
        if e.parser_type == "manual":
            m.manual += 1
            for rule in set(e.postprocess_applied or []):  # linked via repair_of (PM #23)
                m.rule_repaired[rule] += 1
                if RULE_TARGET.get(rule) == "credit" and e.credit is not None:
                    m.rule_repair_credit[(rule, e.credit)] += 1
            continue
        m.rule_applied.update(e.postprocess_applied or [])
        is_llm = e.parser_type == "llm" or bool(e.is_fallback_used)
        if is_llm:
            m.llm_attempts += 1
            m.llm_fallbacks += int(bool(e.is_fallback_used))
        else:
            m.rule_only += 1
        if e.processing_time_ms is not None:
            (llm_times if is_llm else rule_times).append(e.processing_time_ms)
    m.llm_p50_ms, m.llm_p95_ms = percentile(llm_times, 50), percentile(llm_times, 95)
    m.rule_p50_ms, m.rule_p95_ms = percentile(rule_times, 50), percentile(rule_times, 95)

    rows = db.execute(select(LedgerEntry.id, LedgerEntry.parser_type, LedgerEntry.postprocess_applied)).all()
    parser_of = {rid: parser for rid, parser, _ in rows}
    rules_of = {rid: rules or [] for rid, _, rules in rows}
    for ev in db.scalars(select(FeedbackEvent)):
        if not _in_period(ev.created_at, start, end):
            continue
        if parser_of.get(ev.ledger_entry_id) == "manual":
            # values the user typed in the 400 modal are not "AI got it wrong" — counted apart
            m.manual_decisions += int(ev.action != "reject")
            continue
        if ev.action == "reject":
            m.rejects += 1
            continue
        m.decisions += 1
        diff = ev.corrected_data or {}
        for name in ("counterparty", "total_amount", "credit"):
            if name in diff:
                kind = _kind(diff[name].get("before"), diff[name].get("after"))
                if kind:
                    {"change": m.field_changes, "clear": m.field_clears, "fill": m.field_fills}[kind][name] += 1
                if kind in ("change", "clear"):
                    m.field_wrong[name] += 1
        kinds = _item_kinds(diff)
        m.item_key_changes.update(kinds["change"])
        m.item_key_clears.update(kinds["clear"])
        m.item_key_fills.update(kinds["fill"])
        for kind, counter in (("change", m.field_changes), ("clear", m.field_clears), ("fill", m.field_fills)):
            if kinds[kind]:
                counter["items"] += 1
        if kinds["change"] or kinds["clear"]:
            m.field_wrong["items"] += 1
        _count_rules(m, rules_of.get(ev.ledger_entry_id, []), diff, kinds["change"] | kinds["clear"] | kinds["fill"])
    return m


def _count_rules(m: PeriodMetrics, rules: list[str], diff: dict[str, Any], touched_item_keys: set[str]) -> None:
    """Per rule that fired on this decided candidate: did the user correct what the rule produced?"""
    for rule in set(rules):
        m.rule_decided[rule] += 1
        if rule.startswith("cascade_"):
            # the operating model's value it emptied is not stored, so "the user corrected it" cannot be told —
            # the report counts how often the cascade asked, not a correction rate (병철 #226)
            continue
        target = RULE_TARGET.get(rule)
        if target == "credit" and "credit" in diff:
            after = diff["credit"].get("after")
            if after is not None and after != diff["credit"].get("before"):
                m.rule_corrected[rule] += 1
                m.rule_credit_after[(rule, after)] += 1
        elif target == "items" and touched_item_keys & {"unit_price", "amount"}:
            m.rule_corrected[rule] += 1
        elif target == "total_amount" and "total_amount" in diff:
            if diff["total_amount"].get("after") != diff["total_amount"].get("before"):
                m.rule_corrected[rule] += 1


def default_periods(now: datetime | None = None) -> list[tuple[str, date, date]]:
    """오늘 · 최근 7일 (KST, today included)."""
    today = (now or datetime.now(timezone.utc)).astimezone(KST).date()
    return [("오늘", today, today), ("최근 7일", today - timedelta(days=6), today)]


def count_log_errors(lines: Iterable[str]) -> Counter:
    """Count `api_error` log lines by ("METHOD route", status, error_code)."""
    counts: Counter = Counter()
    for line in lines:
        match = _API_ERROR.search(line)
        if match:
            method, route, status, code = match.groups()
            counts[(f"{method} {route}", int(status), code)] += 1
    return counts


def _repair_choice(p: PeriodMetrics, rule: str) -> str:
    """미수금 / 결제 완료 (미수금 비율) — the share the rule saved from being recorded as paid."""
    credit, paid = p.rule_repair_credit[(rule, True)], p.rule_repair_credit[(rule, False)]
    share = f" ({_pct(credit / (credit + paid))} 미수금)" if credit + paid else ""
    return f"{credit} / {paid}{share}"


def _ms(value: int | None) -> str:
    return "-" if value is None else f"{value}ms"


def count_postprocess_400(lines: Iterable[str]) -> Counter:
    """(rule, error_code) → 400s where that post-process rule had fired (PM #23)."""
    counts: Counter = Counter()
    for line in lines:
        match = _PP_400.search(line)
        if match:
            for rule in match.group(2).split(","):
                counts[(rule, match.group(3))] += 1
    return counts


def count_voice_answers(lines: Iterable[str]) -> Counter:
    """DL-31 음성 답: (field, "ok"|"failed", reason) → count from `voice_answer` log lines ("-" = no reason)."""
    counts: Counter = Counter()
    for line in lines:
        match = _VOICE_ANSWER.search(line)
        if match:
            counts[match.groups()] += 1
    return counts


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value * 100:.1f}%"


def _render_voice_answers(counts: Counter) -> list[str]:
    """Per field: requests · 성공률 · 실패 이유별 건수. Each request is one voice try (re-asks count again)."""
    lines = ["", "## 음성 되묻기 답 (DL-31, 로그 기준 — 칸별 성공률)",
             "요청 1건 = 말로 한 번 답함 (다시 묻기도 1건씩). 실패 = 버튼·키패드로 넘어가거나 다시 물은 건",
             "| 칸 | 요청 | 성공 | 성공률 | 실패 이유 (건수) |", "|---|---:|---:|---:|---|"]
    fields = list(VOICE_FIELDS) + sorted({f for f, _, _ in counts} - set(VOICE_FIELDS))
    for name in fields:
        ok = sum(n for (f, s, _), n in counts.items() if f == name and s == "ok")
        failed = Counter({r: n for (f, s, r), n in counts.items() if f == name and s == "failed"})
        total = ok + sum(failed.values())
        reasons = " · ".join(f"{r} {n}" for r, n in sorted(failed.items(), key=lambda kv: (-kv[1], kv[0]))) or "-"
        lines.append(f"| {name} | {total} | {ok} | {_pct(ok / total if total else None)} | {reasons} |")
    return lines


def render(periods: list[PeriodMetrics], log_errors: Counter | None, pp_400: Counter | None = None,
           voice_answers: Counter | None = None) -> str:
    """Plain markdown tables — paste into Notion/reports as-is."""
    head = "| 지표 | " + " | ".join(p.label for p in periods) + " |"
    sep = "|---|" + "---:|" * len(periods)

    def row(name: str, fn) -> str:
        return f"| {name} | " + " | ".join(fn(p) for p in periods) + " |"

    lines = [
        "## 파싱",
        head, sep,
        row("후보 저장 (200)", lambda p: str(p.candidates)),
        row("LLM 시도", lambda p: str(p.llm_attempts)),
        row("LLM → Rule 폴백", lambda p: f"{p.llm_fallbacks} ({_pct(p.fallback_rate)})"),
        row("Rule 단독 (LLM 꺼짐)", lambda p: str(p.rule_only)),
        row("보완 제출 (manual)", lambda p: str(p.manual)),
        row("처리 시간 p50 (LLM 시도)", lambda p: _ms(p.llm_p50_ms)),
        row("처리 시간 p95 (LLM 시도)", lambda p: _ms(p.llm_p95_ms)),
        row("처리 시간 p50 (Rule 단독)", lambda p: _ms(p.rule_p50_ms)),
        row("처리 시간 p95 (Rule 단독)", lambda p: _ms(p.rule_p95_ms)),
        "",
        "## 사용자 수정·보완 (AI 후보의 확정·수정 결정 기준, 수동 폼 제외)",
        "수정률 = AI가 채운 값이 틀린 비율(바꿈 + 지움) · 채움 = AI가 비운 값을 사용자가 채운 건수",
        head, sep,
        row("확정·수정 결정 (AI 후보)", lambda p: str(p.decisions)),
        row("거절 (AI 후보)", lambda p: str(p.rejects)),
        row("보완 제출 건 확정 (제외)", lambda p: str(p.manual_decisions)),
        row("거래처 수정률", lambda p: _pct(p.correction_rate("counterparty"))),
        row("품목 수정률 (하나라도 바꿈·지움)", lambda p: _pct(p.correction_rate("items"))),
        *(row(f"  └ 품목 {k} 바꿈 / 지움 / 채움",
              lambda p, k=k: f"{p.item_key_changes[k]} / {p.item_key_clears[k]} / {p.item_key_fills[k]}")
          for k in ITEM_KEYS),
        row("합계 금액 수정률", lambda p: _pct(p.correction_rate("total_amount"))),
        row("외상 여부 수정률 (AI 값을 반대로)", lambda p: _pct(p.correction_rate("credit"))),
        row("외상 선택 (AI 비움 → 사용자 선택)", lambda p: str(p.field_fills["credit"])),
        "",
        "## 후처리 규칙 (PM #23 — 규칙이 만든 값을 사용자가 고친 비율)",
        head, sep,
        *(row(f"{rule} 적용 / 결정 / 고침",
              lambda p, rule=rule: f"{p.rule_applied[rule]} / {p.rule_decided[rule]} / "
              + ("-" if rule.startswith("cascade_") else f"{p.rule_corrected[rule]}"
                 + (f" ({_pct(p.rule_corrected[rule] / p.rule_decided[rule])})" if p.rule_decided[rule] else "")))
          for rule in RULE_TARGET),
        row("  └ credit_from_text 뒤 사용자가 바꾼 값 (→외상 / →현금)",
            lambda p: f"{p.rule_credit_after[('credit_from_text', True)]} / "
                      f"{p.rule_credit_after[('credit_from_text', False)]}"),
        row("보완 제출로 연결된 건 (repair_of, 규칙 → 400 → 사용자 보완)",
            lambda p: str(sum(p.rule_repaired.values()))),
        row("  └ 외상 비운 뒤 사용자 선택 (미수금 / 결제 완료)",
            lambda p: _repair_choice(p, "credit_cleared_no_word")),
        "※ credit_cleared_no_word는 항상 400(후보 저장 안 됨) → 위 적용 표엔 0. 400 횟수는 로그 표,",
        "   사용자가 고른 값은 FE가 보완 제출에 repair_of를 보낸 건만 연결돼요.",
        "",
        "참고: 400 응답은 저장하지 않으므로 파싱 지표는 성공(200) 후보 기준이다.",
    ]
    if log_errors is not None:
        lines += ["", "## 오류 응답 (로그 기준, 모든 4xx·5xx)", "| 엔드포인트 | 상태 | error_code | 건수 |", "|---|---:|---|---:|"]
        if not log_errors:
            lines.append("| - | - | - | 0 |")
        for (scope, status, code), n in sorted(log_errors.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| {scope} | {status} | {code} | {n} |")
        total = Counter()
        for (_, status, _), n in log_errors.items():
            total[status] += n
        # 503 = 외부 서비스 실패 (DL-33, 11/2까지 504) — 바뀌기 전 로그도 같은 줄에 센다
        lines.append(f"\n400 합계 {total[400]} · 외부 서비스 실패(503·504) 합계 {total[503] + total[504]}")
    if pp_400 is not None:
        lines += ["", "## 후처리 규칙 때문에 400이 된 횟수 (로그 기준 — 저장되지 않는 건)",
                  "| 규칙 | error_code | 건수 |", "|---|---|---:|"]
        if not pp_400:
            lines.append("| - | - | 0 |")
        for (rule, code), n in sorted(pp_400.items(), key=lambda kv: (-kv[1], kv[0])):
            lines.append(f"| {rule} | {code} | {n} |")
    if voice_answers is not None:
        lines += _render_voice_answers(voice_answers)
    return "\n".join(lines)
