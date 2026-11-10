import { test } from "node:test";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { invoiceForm, splitAmount } from "../src/utils/taxInvoiceForm";
import { TaxInvoiceSheet } from "../src/ui/TaxInvoiceSheet";
import type { InvoiceItemView, TaxInvoiceDetail } from "../src/ui/types";

const item = (name: string, amount: number | null, qty: string | null = "3", unit: string | null = "박스"): InvoiceItemView =>
  ({ name, qty, unit, unitPrice: null, amount });

test("면세 / 부가세 포함 / 부가세 별도 split one amount in whole won", () => {
  assert.deepEqual(splitAmount(44000, "exempt"), { supply: 44000, tax: 0 });
  assert.deepEqual(splitAmount(44000, "included"), { supply: 40000, tax: 4000 });
  assert.deepEqual(splitAmount(44000, "excluded"), { supply: 44000, tax: 4400 });
  // 포함: supply rounded, tax is the rest — the two always add back to the ledger amount
  const odd = splitAmount(10001, "included");
  assert.equal(odd.supply + odd.tax, 10001);
  assert.deepEqual(splitAmount(10005, "excluded"), { supply: 10005, tax: 1000 });
});

test("per-line amounts are split line by line when they add up to the total", () => {
  const form = invoiceForm([item("배추", 30000), item("무", 14000)], 44000, "included");
  assert.equal(form.title, "세금계산서");
  assert.deepEqual(form.lines.map((l) => [l.supply, l.tax]), [[27273, 2727], [12727, 1273]]);
  assert.equal(form.supply + form.tax, 44000);
  assert.equal(form.total, 44000);
});

test("missing item amounts never get invented — totals come from the entry total", () => {
  const form = invoiceForm([item("배추", null), item("무", null)], 44000, "excluded");
  assert.deepEqual(form.lines.map((l) => [l.supply, l.tax]), [[null, null], [null, null]]);
  assert.deepEqual([form.supply, form.tax, form.total], [44000, 4400, 48400]);
});

test("면세 is a 계산서 with no tax", () => {
  const form = invoiceForm([item("배추", 30000)], 30000, "exempt");
  assert.deepEqual([form.title, form.tax, form.total], ["계산서", 0, 30000]);
});

const detail: TaxInvoiceDetail = { counterparty: "가나상회", dateLabel: "2026. 10. 26. (월)", items: [item("배추", 44000)], total: 44000, credit: true };

test("the sheet says it is a mock and shows no approval number", () => {
  const html = renderToStaticMarkup(<TaxInvoiceSheet detail={detail} business={{ name: "솔바람식자재", numberMasked: "123-45-*****" }}
    kind="included" onKind={() => {}} onClose={() => {}} />);
  assert.match(html, /모의 발행/);
  assert.match(html, /실제로 국세청에 보내지 않아요/);
  assert.doesNotMatch(html, /승인번호/);
  assert.match(html, /이 금액을 청구함/);
  assert.match(html, /40,000원/);
  assert.match(html, /4,000원/);
});

test("부가세 별도 explains that 받을 돈 does not change", () => {
  const html = renderToStaticMarkup(<TaxInvoiceSheet detail={detail} business={null} kind="excluded" onKind={() => {}} onClose={() => {}} />);
  assert.match(html, /48,400원/);
  assert.match(html, /받을 돈은 장부 금액 그대로/);
  assert.match(html, /사업자 정보 미등록/);
});
