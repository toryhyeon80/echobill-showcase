/**
 * View models for the design components. They are plain display data —
 * pages map API/store types (CandidateSnapshot etc.) into these, so the design layer
 * never imports api/ or stores/.
 */

export interface ItemView {
  name: string | null;
  qty: number | null;
  unit: string | null;
  unitPrice: number | null;
  amount: number | null;
}

export interface CandidateView {
  counterparty: string | null;
  items: ItemView[];
  total: number | null;
  /** true = 미수금, false = 결제 완료, null = 사용자가 골라야 함 */
  credit: boolean | null;
  /** e.g. "10월 8일 (수) · 매출" */
  dateLabel: string;
  /** What the user said (text input or STT transcript). */
  utterance: string;
}

/** DL-31 clarification fields. */
export type ClarifyField = "credit" | "qty" | "total" | "counterparty" | "mismatch";

export interface ClarifyQuestion {
  field: ClarifyField;
  /** Big question text, e.g. "외상이에요, 현금이에요?" */
  question: string;
  /** Transcript when the original input was voice (DL-29), shown as "이렇게 들렸어요". */
  heard?: string | null;
  /** Item name for qty questions, e.g. "배추". */
  itemName?: string | null;
  /** Unit for qty questions, e.g. "박스". */
  unit?: string | null;
  /** Two amounts for 합계 불일치 (mismatch). */
  choices?: [number, number];
  /** Amount candidates heard in the sentence (total). */
  amountHints?: number[];
  /** Recent counterparties (counterparty). */
  recentCounterparties?: string[];
  /** Server-side voice answer enabled for this field (VOICE_ANSWER_FIELDS). Hide mic when false. */
  voiceEnabled: boolean;
}

export const won = (value: number | null | undefined): string =>
  value == null ? "-" : `${value.toLocaleString("ko-KR")}원`;

/* ---- 장부 (LedgerView) ---- */

export interface LedgerEntryView {
  id: string;
  counterparty: string;
  /** e.g. "배추 3박스 · 무 2개" */
  summary: string;
  amount: number;
  /** true = 미수금, false = 결제 완료 */
  credit: boolean;
}

/** Calendar dots per day of the shown month (amounts are never shown in cells). */
export interface DayMarks {
  unpaid?: boolean;
  paid?: boolean;
}

export interface CounterpartyBalanceView {
  counterparty: string;
  unpaid: number;
  /** Days since the oldest still-owed 외상 (KST, FIFO) — "32일 경과" (PM 10/19). Omit until the API sends it. */
  oldestDays?: number;
  /** 외상 entries still owed, for "· 외상 2건". */
  owedCount?: number;
}

/** 거래처 정산 내역: credit entries oldest first, deposits applied to the oldest first (FIFO). */
export interface SettlementView {
  counterparty: string;
  creditTotal: number;
  paidTotal: number;
  unpaid: number;
  /** Days since the oldest still-owed entry — "가장 오래된 외상 32일 경과". Omit when unknown. */
  oldestDays?: number;
  /** True only when the oldest still-owed row is a moved credit with an estimated date. */
  oldestDaysApproximate?: boolean;
  entries: { id: string; dateLabel: string; messageDateLabel?: string; total: number; allocated: number; remaining: number }[];
  payments: { dateLabel: string; amount: number }[];
}

/* ---- 세금계산서 (모의 발행) ---- */

/** none = 아직 발행 안 함 · requested = 발행 중(모의 지연) · issued = 모의 발행 완료 · failed = 실패 */
export type InvoiceStatus = "none" | "requested" | "issued" | "failed";

export interface InvoiceRowView {
  entryId: string;
  dateLabel: string;
  counterparty: string;
  amount: number;
  status: InvoiceStatus;
  failReason?: string | null;
  /** Items and credit of the ledger entry, for the 모의 양식 sheet; absent for older invoices without an entry. */
  detail?: TaxInvoiceDetail;
}

/** One ledger item as the 모의 양식 shows it (qty may arrive as a decimal string). */
export interface InvoiceItemView {
  name: string | null;
  qty: string | number | null;
  unit: string | null;
  unitPrice: number | null;
  amount: number | null;
}

/** What the 모의 세금계산서 양식 needs from one confirmed entry. */
export interface TaxInvoiceDetail {
  counterparty: string;
  /** 작성일자 — the ledger's KST registration day. */
  dateLabel: string;
  items: InvoiceItemView[];
  total: number;
  credit: boolean | null;
}
