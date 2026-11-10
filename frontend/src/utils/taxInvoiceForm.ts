import type { InvoiceItemView } from "../ui/types";
import { localStore } from "./browserStorage";

/**
 * 모의 세금계산서 양식 계산 (PM 10/24·10/26) — screen only, nothing is stored on the server.
 * 면세 = 계산서(세액 0, 미가공 농수산물 등) · 부가세 포함 = 장부 금액 안에 세액 · 부가세 별도 = 장부 금액 위에 10%.
 * The ledger amount (받을 돈) never changes; with 부가세 별도 the document total is larger than what is owed.
 */
export type TaxKind = "exempt" | "included" | "excluded";

export const TAX_KIND_LABEL: Record<TaxKind, string> = { exempt: "면세", included: "부가세 포함", excluded: "부가세 별도" };

interface InvoiceLine {
  name: string;
  qty: string;
  unitPrice: number | null;
  supply: number | null;
  tax: number | null;
}

interface InvoiceForm {
  title: "계산서" | "세금계산서";
  lines: InvoiceLine[];
  supply: number;
  tax: number;
  total: number;
}

/** One amount split into 공급가액 and 세액 (whole won; 포함 rounds the supply, 별도 drops the fraction of the tax). */
export function splitAmount(amount: number, kind: TaxKind): { supply: number; tax: number } {
  if (kind === "exempt") return { supply: amount, tax: 0 };
  if (kind === "included") {
    const supply = Math.round((amount * 10) / 11);
    return { supply, tax: amount - supply };
  }
  return { supply: amount, tax: Math.floor(amount / 10) };
}

/**
 * Lines and totals for one confirmed entry. When every item has an amount and they add up to the total, each line is
 * split on its own (so the lines add up to the totals); otherwise the lines show names only and the totals come from the
 * entry total — never an invented per-item amount.
 */
export function invoiceForm(items: InvoiceItemView[], total: number, kind: TaxKind): InvoiceForm {
  const amounts = items.map((item) => item.amount);
  const perLine = items.length > 0 && amounts.every((a): a is number => typeof a === "number")
    && amounts.reduce((sum, a) => sum + a, 0) === total;
  const lines = items.map((item) => {
    const split = perLine ? splitAmount(item.amount as number, kind) : null;
    return { name: item.name ?? "", qty: item.qty == null ? "" : `${item.qty}${item.unit ?? ""}`,
      unitPrice: item.unitPrice, supply: split?.supply ?? null, tax: split?.tax ?? null };
  });
  const sums = perLine
    ? { supply: lines.reduce((s, l) => s + (l.supply ?? 0), 0), tax: lines.reduce((s, l) => s + (l.tax ?? 0), 0) }
    : splitAmount(total, kind);
  return { title: kind === "exempt" ? "계산서" : "세금계산서", lines, ...sums, total: sums.supply + sums.tax };
}

const KIND_KEY = "echobill_tax_kind";
/** This device's last choice (a per-viewer convenience, not a record); 면세 until chosen — 주 사용자가 농수산물 납품. */
export function savedTaxKind(): TaxKind {
  const value = localStore.getItem(KIND_KEY);
  return value === "included" || value === "excluded" ? value : "exempt";
}
export function saveTaxKind(kind: TaxKind): void {
  localStore.setItem(KIND_KEY, kind);
}
