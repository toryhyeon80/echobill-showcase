import { isValidAmount } from "./amountLimits";
import { exceedsTextLimit, NAME_MAX_CHARS, UNIT_MAX_CHARS } from "./textLimits";
import { localStore, sessionStore } from "./browserStorage";
import type { CandidateFields, CandidateSnapshot, LedgerItem, PartialCandidate } from "../types/api";
import { readDraft, writeDraft } from "./draftStorage";

const DRAFT_KEY = "echobill_candidate_draft";

/** Editable strings keep an empty field distinct from a server supplied null. */
export interface EditableItem {
  name: string;
  qty: string;
  unit: string;
  unit_price: string;
  amount: string;
}

export interface CandidateDraft {
  candidateId: string | null;
  counterparty: string;
  items: EditableItem[];
  total_amount: string;
  credit: boolean | null;
}

const textValue = (value: string | number | null) => value == null ? "" : String(value);

/** Build an editable copy while preserving omitted values as blank fields. */
export function createCandidateDraft(candidate: CandidateFields, candidateId: string | null = null): CandidateDraft {
  return {
    candidateId,
    counterparty: candidate.counterparty ?? "",
    items: candidate.items.map((item: LedgerItem) => ({
      name: item.name,
      qty: textValue(item.qty),
      unit: item.unit ?? "",
      unit_price: textValue(item.unit_price),
      amount: textValue(item.amount),
    })),
    total_amount: textValue(candidate.total_amount),
    credit: candidate.credit,
  };
}

/** Put the server's extracted 400 fields into an editable, unsaved draft. */
export function createRepairDraft(partial: PartialCandidate, problemFields: string[]): CandidateDraft {
  const itemIndexes = problemFields.flatMap((path) => {
    const match = /^items\[(\d+)\]/.exec(path);
    return match ? [Number(match[1])] : [];
  });
  const count = Math.min(3, Math.max(partial.items?.length ?? 0, ...itemIndexes.map((index) => index + 1), 1));
  const items: LedgerItem[] = Array.from({ length: count }, (_, index) => ({
    name: partial.items?.[index]?.name ?? "",
    qty: partial.items?.[index]?.qty ?? null,
    unit: partial.items?.[index]?.unit ?? null,
    unit_price: partial.items?.[index]?.unit_price ?? null,
    amount: partial.items?.[index]?.amount ?? null,
  }));
  return createCandidateDraft({
    counterparty: partial.counterparty ?? null,
    items,
    total_amount: partial.total_amount ?? null,
    credit: partial.credit ?? null,
  });
}

/** Convert form strings back to the parse correction contract. */
export function draftToCandidateFields(draft: CandidateDraft): CandidateFields {
  const numeric = (value: string) => value.trim() === "" ? null : Number(value);
  return {
    counterparty: draft.counterparty.trim() || null,
    items: draft.items.map((item) => ({
      name: item.name.trim(),
      qty: numeric(item.qty),
      unit: item.unit.trim() || null,
      unit_price: numeric(item.unit_price),
      amount: numeric(item.amount),
    })),
    total_amount: numeric(draft.total_amount),
    credit: draft.credit,
  };
}

/** Restore edits only for the candidate that produced them. */
export function restoreCandidateDraft(candidate: CandidateSnapshot): CandidateDraft {
  try {
    const stored = readDraft(localStore, DRAFT_KEY) ?? readDraft(sessionStore, DRAFT_KEY);
    if (stored) {
      const draft: unknown = stored;
      if (
        draft && typeof draft === "object" &&
        "candidateId" in draft && draft.candidateId === candidate.entry_id &&
        "counterparty" in draft && typeof draft.counterparty === "string" &&
        "total_amount" in draft && typeof draft.total_amount === "string" &&
        "credit" in draft && (draft.credit === null || typeof draft.credit === "boolean") &&
        "items" in draft && Array.isArray(draft.items) && draft.items.every((item: unknown) =>
          !!item && typeof item === "object" && ["name", "qty", "unit", "unit_price", "amount"].every((key) =>
            key in item && typeof (item as Record<string, unknown>)[key] === "string"))
      ) {
        return draft as CandidateDraft;
      }
    }
  } catch {
    localStore.removeItem(DRAFT_KEY);
    sessionStore.removeItem(DRAFT_KEY);
  }
  return createCandidateDraft(candidate, candidate.entry_id);
}

/** Keep in-progress edits across refresh without committing a transaction. */
export function saveCandidateDraft(draft: CandidateDraft): void {
  writeDraft(localStore, DRAFT_KEY, draft);
  sessionStore.removeItem(DRAFT_KEY);
}

export function clearCandidateDraft(): void {
  localStore.removeItem(DRAFT_KEY);
  sessionStore.removeItem(DRAFT_KEY);
}

/** Round qty × unit price to whole won, with 0.5 won rounded up (D04). */
export function calculatedWon(item: EditableItem): number | null {
  if (!/^\d+(?:\.\d{1,2})?$/.test(item.qty) || !/^\d+$/.test(item.unit_price)) {
    return null;
  }
  const quantity = Number(item.qty);
  const unitPrice = Number(item.unit_price);
  const [whole, fraction = ""] = item.qty.split(".");
  const hundredths = Number(whole) * 100 + Number(fraction.padEnd(2, "0"));
  const amountInHundredths = hundredths * unitPrice;
  const amount = Math.floor((amountInHundredths + 50) / 100);
  return quantity > 0 && quantity <= 100_000 && unitPrice >= 1 && unitPrice <= 100_000_000 &&
    Number.isSafeInteger(amountInHundredths) ? amount : null;
}

/** Recalculate only when every item amount is explicitly available. */
export function recalculateDraftTotal(draft: CandidateDraft): CandidateDraft {
  const amounts = draft.items.map((item) => /^\d+$/.test(item.amount) ? Number(item.amount) : null);
  if (!amounts.every((amount) => amount !== null)) return draft;
  const total = amounts.reduce<number>((sum, amount) => sum + (amount ?? 0), 0);
  return Number.isSafeInteger(total) ? { ...draft, total_amount: String(total) } : draft;
}

/** Update the displayed amount and total when the user's edit makes them calculable. */
export function updateDraftItem(
  draft: CandidateDraft,
  index: number,
  field: keyof EditableItem,
  value: string,
): CandidateDraft {
  const items = draft.items.map((item, itemIndex) => {
    if (itemIndex !== index) return item;
    const next = { ...item, [field]: value };
    if (field === "qty" || field === "unit_price") {
      const calculated = calculatedWon(next);
      if (calculated !== null) next.amount = String(calculated);
    }
    return next;
  });
  const shouldRecalculate = field === "qty" || field === "unit_price" || field === "amount";
  const next = { ...draft, items };
  return shouldRecalculate ? recalculateDraftTotal(next) : next;
}

export interface DraftProblem { field: string; message: string }

/** Keep messages and their DL-01 field paths together for both summary and inline feedback. */
export function draftProblems(draft: CandidateDraft): DraftProblem[] {
  const issues: DraftProblem[] = [];
  const add = (field: string, message: string) => issues.push({ field, message });
  if (!draft.counterparty.trim()) add("counterparty", "거래처를 입력해 주세요.");
  else if (exceedsTextLimit(draft.counterparty, NAME_MAX_CHARS)) add("counterparty", `거래처는 ${NAME_MAX_CHARS}자까지 입력해 주세요.`);
  if (draft.items.length === 0) add("items", "품목을 하나 이상 입력해 주세요.");
  if (draft.items.length > 3) add("items", "품목은 최대 3개까지 입력할 수 있습니다.");
  draft.items.forEach((item, index) => {
    const label = `${index + 1}번째 품목`;
    if (!item.name.trim()) add(`items[${index}].name`, `${label} 이름을 입력해 주세요.`);
    else if (exceedsTextLimit(item.name, NAME_MAX_CHARS)) add(`items[${index}].name`, `${label} 이름은 ${NAME_MAX_CHARS}자까지 입력해 주세요.`);
    if (exceedsTextLimit(item.unit, UNIT_MAX_CHARS)) add(`items[${index}].unit`, `${label} 단위는 ${UNIT_MAX_CHARS}자까지 입력해 주세요.`);
    if (!/^\d+(?:\.\d{1,2})?$/.test(item.qty) || Number(item.qty) <= 0 || Number(item.qty) > 100_000) {
      add(`items[${index}].qty`, `${label} 수량을 확인해 주세요.`);
    }
    if (item.unit_price && !isValidAmount(item.unit_price)) {
      add(`items[${index}].unit_price`, `${label} 단가는 1원부터 1억 원까지 입력해 주세요.`);
    }
    if (item.amount && !isValidAmount(item.amount)) {
      add(`items[${index}].amount`, `${label} 금액은 1원부터 1억 원까지 입력해 주세요.`);
    }
    const calculated = calculatedWon(item);
    if (calculated !== null && item.amount !== "" && Number(item.amount) !== calculated) {
      add(`items[${index}].amount`, `${label} 수량×단가와 품목 금액이 다릅니다.`);
    }
  });
  if (!isValidAmount(draft.total_amount)) {
    add("total_amount", "거래 총액은 1원부터 1억 원까지 입력해 주세요.");
  }
  if (draft.credit === null) add("credit", "외상 여부를 선택해 주세요.");
  const amounts = draft.items.map((item) => /^\d+$/.test(item.amount) ? Number(item.amount) : null);
  if (amounts.length > 0 && amounts.every((amount) => amount !== null)) {
    const sum = amounts.reduce<number>((total, amount) => total + (amount ?? 0), 0);
    if (sum !== Number(draft.total_amount)) add("total_amount", "품목 금액 합계와 거래 총액이 다릅니다.");
  }
  return issues;
}

/** Compatibility for repair flows that need the same ordered message list. */
export function draftIssues(draft: CandidateDraft): string[] {
  return draftProblems(draft).map((problem) => problem.message);
}
