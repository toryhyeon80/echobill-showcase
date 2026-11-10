import { calculatedWon, type CandidateDraft } from "./candidateDraft";
import type { VoiceAnswer, VoiceAnswerField } from "../api/voiceAnswer";
import { exceedsTextLimit, NAME_MAX_CHARS, UNIT_MAX_CHARS } from "./textLimits";

export type ClarificationField = "credit" | "counterparty" | "total_amount" | `items[${number}].qty`;

/** Offer two totals only when every line has an explicit or exactly calculable amount. */
export function arithmeticChoices(draft: CandidateDraft): number[] {
  if (pendingClarifications(draft).length || !draft.items.length) return [];
  const amounts = draft.items.map((item) => {
    const calculated = calculatedWon(item);
    const stated = /^\d+$/.test(item.amount) ? Number(item.amount) : null;
    if (calculated !== null && stated !== null && calculated !== stated) return null;
    return stated ?? calculated;
  });
  if (amounts.some((amount) => amount === null)) return [];
  const sum = amounts.reduce<number>((total, amount) => total + (amount ?? 0), 0);
  const stated = Number(draft.total_amount);
  return sum >= 1 && sum <= 100_000_000 && sum !== stated ? [stated, sum] : [];
}

/** Map API names without changing the ledger field names or item index. */
export function voiceField(field: ClarificationField): VoiceAnswerField {
  return field === "total_amount" ? "total" : field.startsWith("items[") ? "qty" : field as "credit" | "counterparty";
}

/** Reject mismatched or invalid answers instead of silently filling a different field. */
export function applyVoiceAnswer(draft: CandidateDraft, field: ClarificationField, answer: VoiceAnswer): CandidateDraft | null {
  if (answer.status !== "ok" || answer.field !== voiceField(field)) return null;
  const value = answer.value;
  if (field === "credit" && typeof value !== "boolean") return null;
  if (field === "counterparty" && (typeof value !== "string" || !value.trim() || exceedsTextLimit(value, NAME_MAX_CHARS))) return null;
  if (field !== "credit" && field !== "counterparty" && typeof value !== "number" && typeof value !== "string") return null;
  if (value === null) return null;
  let next = answerClarification(draft, field, typeof value === "boolean" ? value : String(value));
  if (pendingClarifications(next).includes(field)) return null;
  if (field.startsWith("items[") && answer.unit != null) {
    if (typeof answer.unit !== "string" || exceedsTextLimit(answer.unit, UNIT_MAX_CHARS)) return null;
    const index = Number(/\[(\d+)\]/.exec(field)?.[1]);
    next = { ...next, items: next.items.map((item, i) => i === index ? { ...item, unit: answer.unit! } : item) };
  }
  return next;
}

/** List unanswered DL-31 fields; never infer a quantity or payment choice. */
export function pendingClarifications(draft: CandidateDraft): ClarificationField[] {
  const fields: ClarificationField[] = [];
  if (draft.credit === null) fields.push("credit");
  draft.items.forEach((item, index) => {
    if (!/^\d+(?:\.\d{1,2})?$/.test(item.qty) || Number(item.qty) <= 0 || Number(item.qty) > 100_000) fields.push(`items[${index}].qty`);
  });
  if (!/^\d+$/.test(draft.total_amount) || Number(draft.total_amount) < 1 || Number(draft.total_amount) > 100_000_000) fields.push("total_amount");
  if (!draft.counterparty.trim() || exceedsTextLimit(draft.counterparty, NAME_MAX_CHARS)) fields.push("counterparty");
  return fields;
}

/** Describe one question, using only item names and units already heard. */
export function clarificationQuestion(field: ClarificationField, draft: CandidateDraft): string {
  if (field === "credit") return "외상이에요, 현금이에요?";
  if (field === "counterparty") return "어느 집이에요?";
  if (field === "total_amount") return "전부 얼마예요?";
  const index = Number(/\[(\d+)\]/.exec(field)?.[1]);
  const item = draft.items[index];
  const name = item?.name.trim() || `${index + 1}번째 품목`;
  return item?.unit.trim() ? `${name} 몇 ${item.unit.trim()}이에요?` : `${name} 수량이 얼마예요?`;
}

/** Fill just the question's field, leaving unrelated extracted values unchanged. */
export function answerClarification(draft: CandidateDraft, field: ClarificationField, value: string | boolean): CandidateDraft {
  if (field === "credit") return typeof value === "boolean" ? { ...draft, credit: value } : draft;
  if (typeof value !== "string") return draft;
  if (field === "counterparty" || field === "total_amount") return { ...draft, [field]: value };
  const index = Number(/\[(\d+)\]/.exec(field)?.[1]);
  return { ...draft, items: draft.items.map((item, itemIndex) => itemIndex === index ? { ...item, qty: value } : item) };
}
