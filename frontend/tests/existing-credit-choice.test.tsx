import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { renderToStaticMarkup } from "react-dom/server";
import { useExistingCreditChoice } from "../src/utils/useExistingCreditChoice";
import { clearSessionDrafts } from "../src/utils/draftStorage";
import { buildCreditMessage } from "../src/utils/creditMessage";
import { settlementView } from "../src/utils/viewModels";
import { SettlementSheet } from "../src/ui/SettlementSheet";
import { MoveCreditView } from "../src/ui/MoveCreditView";
import "./review-fixture";

function Choice({ owner }: { owner: string | null }) {
  const [skipped, skip] = useExistingCreditChoice(owner);
  return <button data-skipped={skipped} onClick={skip}>choice</button>;
}

test("skip survives a home remount and logout but stays isolated between accounts", async () => {
  localStorage.clear();
  let root!: ReactTestRenderer;
  const skipped = () => root.root.findByType("button").props["data-skipped"];
  try {
    await act(async () => { root = create(<Choice owner="owner-a" />); });
    assert.equal(skipped(), false);
    await act(async () => { root.root.findByType("button").props.onClick(); });
    assert.equal(skipped(), true);
    act(() => root.unmount());
    clearSessionDrafts();
    await act(async () => { root = create(<Choice owner="owner-a" />); });
    assert.equal(skipped(), true);
    await act(async () => { root.update(<Choice owner="owner-b" />); });
    assert.equal(skipped(), false);
    await act(async () => { root.update(<Choice owner={null} />); root.root.findByType("button").props.onClick(); });
    assert.equal(skipped(), false);
    await act(async () => { root.update(<Choice owner="owner-a" />); });
    assert.equal(skipped(), true);
  } finally { act(() => root?.unmount()); localStorage.clear(); }
});

test("only an outstanding moved oldest credit gets approximate age; dates keep their meaning when copied", () => {
  const row = { entry_id: "old", kst_date: "2026-09-15", total_amount: 10000, allocated: 0, remaining: 10000, moved: true };
  const detail = { counterparty: "테스트상회", credit_total: 20000, paid_total: 0, unpaid: 20000, oldest_days: 41,
    entries: [row, { ...row, entry_id: "new", kst_date: "2026-10-26", moved: false }], payments: [] };
  const view = settlementView(detail);
  assert.equal(view.oldestDaysApproximate, true);
  const message = buildCreditMessage(view);
  assert.match(message, /이전 외상 10,000원/);
  assert.doesNotMatch(message, /2026-09-15|옮겨 온 기존 외상/);
  assert.match(view.entries[0].dateLabel, /2026-09-15 무렵/);
  const html = renderToStaticMarkup(<SettlementSheet detail={view} onClose={() => {}} onPayment={() => {}} />);
  assert.match(html, /약 .*41일 경과/);
  const paid = settlementView({ ...detail, entries: [{ ...row, remaining: 0, allocated: 10000 }, detail.entries[1]] });
  assert.equal(paid.oldestDaysApproximate, false);
  const regular = renderToStaticMarkup(<SettlementSheet detail={paid} onClose={() => {}} onPayment={() => {}} />);
  assert.doesNotMatch(regular, /약 /);
  const form = renderToStaticMarkup(<MoveCreditView onBack={() => {}} onSubmit={() => {}} />);
  assert.match(form, /누구한테 받을 돈/);
});
