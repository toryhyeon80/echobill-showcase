import { test } from "node:test";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { PaymentView } from "../src/ui/PaymentView";
import { calendarMarks, isDatedTrade } from "../src/utils/viewModels";
import type { LedgerList } from "../src/types/api";
const snapshot = { entry_id: "draft-entry", utterance_text: "테스트", counterparty: "테스트상사",
  items: [{ name: "배추", qty: 2, unit: null, unit_price: null, amount: 1000 }], total_amount: 1000, credit: true };
test("a payment retry locks the keypad and remains replayable after its server balance changed", () => {
  const props = { balances: [{ counterparty: "테스트상사", unpaid: 0 }], counterparty: "테스트상사", amount: "1000",
    onCounterparty: () => {}, onAmount: () => {}, onBack: () => {}, onSubmit: () => {} };
  const normal = renderToStaticMarkup(<PaymentView {...props} />);
  assert.match(normal, /받을 돈.*보다 많아요/);
  const uncertain = renderToStaticMarkup(<PaymentView {...props} uncertain />);
  assert.match(uncertain, /처리 결과 다시 확인/);
  assert.match(uncertain, /disabled=""[^>]*aria-label="2"/);
  assert.doesNotMatch(uncertain, /보다 많아요/);
});

test("design calendar retains credit occurrence and excludes candidates", () => {
  const entries = [{ ...snapshot, status: "confirmed", created_at: "2026-10-06T00:00:00Z", kst_date: "2026-10-06" },
    { ...snapshot, entry_id: "other", status: "candidate", created_at: "2026-10-07T00:00:00Z", kst_date: "2026-10-07" }] as LedgerList["entries"];
  assert.deepEqual(calendarMarks(entries), { 6: { unpaid: true } });
});
test("a moved credit stays in the list but puts no dot on its approximate day", () => {
  const entries = [{ ...snapshot, status: "confirmed", created_at: "2026-09-15T00:00:00Z", kst_date: "2026-09-15", moved: true },
    { ...snapshot, entry_id: "sale", status: "confirmed", created_at: "2026-09-16T00:00:00Z", kst_date: "2026-09-16", moved: false }] as LedgerList["entries"];
  assert.deepEqual(calendarMarks(entries), { 16: { unpaid: true } });
});
test("date-based lists leave out a moved credit and candidates with one rule (병철 #227)", () => {
  assert.equal(isDatedTrade({ status: "confirmed", moved: false }), true);
  assert.equal(isDatedTrade({ status: "confirmed", moved: true }), false);  // its day is approximate
  assert.equal(isDatedTrade({ status: "candidate", moved: false }), false);
});
