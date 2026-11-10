import type { CandidateDraft } from "./candidateDraft";
import type { LedgerEntry, LedgerList, CounterpartyDetail, UnpaidList } from "../types/api";
import type { CandidateView, CounterpartyBalanceView, DayMarks, LedgerEntryView, SettlementView } from "../ui/types";

/** Use the server's still-owed FIFO age; missing or fully paid ages stay absent. */
export function counterpartyBalanceView(row: UnpaidList["counterparties"][number]): CounterpartyBalanceView {
  return { counterparty: row.counterparty, unpaid: row.unpaid,
    oldestDays: row.unpaid > 0 ? row.oldest_days ?? undefined : undefined, owedCount: row.owed_count };
}

/** Summarize the same server ages as the ledger without fetching every counterparty again. */
export function receivableNote(unpaid: UnpaidList): string {
  const owed = unpaid.counterparties.filter((row) => row.unpaid > 0);
  const oldest = owed.length && owed.every((row) => row.oldest_days != null)
    ? Math.max(...owed.map((row) => row.oldest_days!)) : null;
  return `${owed.length}곳${oldest != null && oldest > 0 ? ` · 최장 ${oldest}일` : ""}`;
}

/** Translate domain fields at the page boundary; UI-only aliases never enter an API body. */
export function candidateView(draft: CandidateDraft, utterance: string, dateLabel: string): CandidateView {
  const numeric = (value: string) => value.trim() && Number.isFinite(Number(value)) ? Number(value) : null;
  return { counterparty: draft.counterparty || null, total: numeric(draft.total_amount), credit: draft.credit, utterance, dateLabel,
    items: draft.items.map((item) => ({ name: item.name || null, qty: numeric(item.qty), unit: item.unit || null,
      unitPrice: numeric(item.unit_price), amount: numeric(item.amount) })) };
}

/** Show confirmed rows without deriving payment state from a later FIFO balance. */
export function ledgerEntryView(entry: LedgerEntry & { moved?: boolean }): LedgerEntryView {
  // 이미 있는 외상 옮기기 (PM 10/23): a moved total has no items
  const summary = entry.moved ? MOVED_LABEL : entry.items?.map((item) => `${item.name} ${item.qty ?? ""}${item.unit ?? ""}`).join(" · ") ?? "";
  return { id: entry.entry_id, counterparty: entry.counterparty ?? "거래처 확인 필요", amount: entry.total_amount ?? 0,
    credit: entry.credit === true, summary };
}

/** How a moved credit total reads in lists (its date is approximate). */
export const MOVED_LABEL = "옮겨 온 기존 외상";

type LedgerRow = LedgerList["entries"][number];

/**
 * A confirmed trade that happened on its date. A moved credit has only an approximate day, not a sale on that day,
 * so every date-based view (달력 점 · 날짜 목록 · 세금계산서) leaves it out with this one rule (PM 10/26, 병철 #214·#227).
 * `moved` is required on the server type, so a caller that forgets it fails to compile.
 */
export function isDatedTrade(entry: Pick<LedgerRow, "status" | "moved">): boolean {
  return entry.status === "confirmed" && !entry.moved;
}

/** Dates come from the server's KST registration date; deposits never erase calendar dots. */
export function calendarMarks(entries: LedgerRow[]): Record<number, DayMarks> {
  const marks: Record<number, DayMarks> = {};
  for (const entry of entries.filter(isDatedTrade)) {
    const day = Number(entry.kst_date.slice(-2));
    marks[day] = { ...marks[day], [entry.credit ? "unpaid" : "paid"]: true };
  }
  return marks;
}

/** Display the server's FIFO allocation, rather than recomputing settlement in the browser. */
export function settlementView(detail: CounterpartyDetail): SettlementView {
  return { counterparty: detail.counterparty, creditTotal: detail.credit_total, paidTotal: detail.paid_total, unpaid: detail.unpaid,
    oldestDays: detail.unpaid > 0 ? detail.oldest_days ?? undefined : undefined,
    oldestDaysApproximate: detail.entries.find((row) => row.remaining > 0)?.moved === true,
    entries: detail.entries.map((row) => ({ id: row.entry_id, dateLabel: row.moved ? `${row.kst_date} 무렵 · ${MOVED_LABEL}` : row.kst_date, messageDateLabel: row.moved ? "이전 외상" : row.kst_date, total: row.total_amount, allocated: row.allocated, remaining: row.remaining })),
    payments: detail.payments.map((row) => ({ dateLabel: row.kst_date, amount: row.amount })) };
}
