import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { PaymentView } from "../src/ui/PaymentView";
import { PaymentSheet } from "../src/components/PaymentSheet";
import { LedgerPage } from "../src/pages/LedgerPage";
import { LedgerView } from "../src/ui/LedgerView";
import { ConfirmPage } from "../src/pages/ConfirmPage";
import { ConfirmView } from "../src/ui/ConfirmView";
import { BottomSheet } from "../src/ui/BottomSheet";
import { CandidateFieldsForm } from "../src/components/CandidateFieldsForm";
import { AmountText } from "../src/ui/AmountText";
import { createCandidateDraft, draftProblems, draftIssues } from "../src/utils/candidateDraft";
import { isValidAmount, MAX_AMOUNT_KRW } from "../src/utils/amountLimits";
import { api } from "../src/api/client";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { snapshot, noop, until } from "./review-fixture";

const label = (node: { children: unknown[] }): string => node.children.filter((child) => typeof child === "string").join("").trim();

test("new payments enforce the one-payment limit while uncertain replay keeps its original amount", async () => {
  for (const value of ["0", "-1", "1.5", "1e8", "100000001", "9007199254740992"]) assert.equal(isValidAmount(value), false, value);
  assert.equal(isValidAmount("1"), true);
  assert.equal(isValidAmount(String(MAX_AMOUNT_KRW)), true);
  let root!: ReactTestRenderer;
  const view = (amount: string, uncertain = false) => <PaymentView balances={[{ counterparty: "상회", unpaid: 200000000 }]} counterparty="상회" amount={amount} uncertain={uncertain} onCounterparty={noop} onAmount={noop} onBack={noop} onSubmit={noop} />;
  try {
    await act(async () => { root = create(view("")); });
    assert.equal(root.root.findAllByType("button").find((node) => label(node) === "전액")!.props.disabled, true);
    await act(async () => root.update(view("200000000")));
    assert.equal(root.root.findAllByType("button").filter((node) => label(node) === "입금 등록").at(-1)!.props.disabled, true);
    assert.match(JSON.stringify(root.toJSON()), /나누어 입력해 주세요/);
    await act(async () => root.update(view("100000000")));
    assert.equal(root.root.findAllByType("button").find((node) => label(node).endsWith("원 입금 등록"))!.props.disabled, false);
    await act(async () => root.update(view("200000000", true)));
    assert.equal(root.root.findAllByType("button").find((node) => label(node) === "처리 결과 다시 확인")!.props.disabled, false);
  } finally { await act(async () => root?.unmount()); }
});

test("ledger submission handler also rejects over-limit new payments even if called directly", async () => {
  const previous = api.defaults.adapter;
  const balance = { counterparty: "상회", credit_total: 200000000, paid_total: 0, unpaid: 200000000 };
  let writes = 0;
  useAuthStore.getState().setCookieSession("payment-limit-owner");
  api.defaults.adapter = async (config) => {
    if (config.method !== "get") writes++;
    return { config, status: 200, statusText: "OK", headers: {}, data: config.url?.endsWith("/unpaid")
      ? { total_unpaid: balance.unpaid, counterparties: [balance] } : { month: "2026-10", entries: [], daily_credit: [] } };
  };
  const router = createMemoryRouter([{ path: "/", element: <LedgerPage /> }]);
  let root!: ReactTestRenderer;
  try {
    await act(async () => { root = create(<RouterProvider router={router} />); });
    act(() => root.root.findByType(LedgerView).props.onPayment());
    act(() => root.root.findByType(PaymentSheet).props.onAmount("200000000"));
    await act(async () => root.root.findByType(PaymentSheet).props.onSubmit());
    assert.equal(writes, 0);
    act(() => root.root.findByType(PaymentSheet).props.onAmount("100000000"));
    await act(async () => { root.root.findByType(PaymentSheet).props.onSubmit(); await until(() => writes === 1 && !root.root.findAllByType(PaymentSheet).length); });
    assert.equal(writes, 1);
  } finally { await act(async () => root?.unmount()); router.dispose(); api.defaults.adapter = previous; }
});

test("draft validation preserves messages and maps missing values and mismatches to editable fields", () => {
  const draft = createCandidateDraft(snapshot);
  draft.items[0].qty = "";
  draft.total_amount = "100000001";
  draft.credit = null;
  const problems = draftProblems(draft);
  for (const path of ["items[0].qty", "total_amount", "credit"]) assert.ok(problems.some((problem) => problem.field === path));
  assert.deepEqual(draftIssues(draft), problems.map((problem) => problem.message));
  assert.equal(draft.items[0].qty, "", "validation never invents a quantity");
});

test("invalid edit stays open, exposes field error text, focuses that field, and closes once corrected", async () => {
  localStorage.clear();
  useAuthStore.getState().setCookieSession("inline-error-owner");
  useCandidateStore.getState().setCandidate(snapshot);
  const previous = api.defaults.adapter;
  let writes = 0, focused = 0, scrolled = 0;
  const selectors: string[] = [];
  api.defaults.adapter = async (config) => {
    if (config.method !== "get") writes++;
    return { config, status: 200, statusText: "OK", headers: {}, data: config.url?.endsWith("/unpaid") ? { counterparties: [] } : { entries: [] } };
  };
  const router = createMemoryRouter([{ path: "/", element: <ConfirmPage /> }]);
  let root!: ReactTestRenderer;
  const done = () => root.root.findAllByType("button").find((node) => label(node) === "수정 완료")!;
  try {
    await act(async () => { root = create(<RouterProvider router={router} />, { createNodeMock: (element) => element.type === "fieldset"
      ? { querySelector: (selector: string) => { selectors.push(selector); return { focus: () => focused++, scrollIntoView: () => scrolled++ }; } } : null }); });
    act(() => root.root.findByType(ConfirmView).props.onEdit());
    act(() => root.root.findByType(CandidateFieldsForm).props.onItem(0, "qty", ""));
    const field = root.root.findAllByType("input").find((node) => node.props["aria-label"] === "수량")!;
    assert.equal(field.props["aria-invalid"], true);
    const message = root.root.findAllByType("span").find((node) => node.props.id === field.props["aria-describedby"]);
    assert.ok(message && label(message).includes("수량을 확인"));
    act(() => done().props.onClick());
    assert.equal(root.root.findByType(BottomSheet).props.open, true);
    assert.equal(focused, 1); assert.equal(scrolled, 1);
    assert.equal(selectors.at(-1), '[aria-invalid="true"]');
    assert.equal(root.root.findByType(ConfirmView).props.validationMessage, undefined, "background does not repeat the edit sheet's live announcement");
    act(() => root.root.findByType(CandidateFieldsForm).props.onItem(0, "qty", "2"));
    assert.equal(root.root.findByType(ConfirmView).props.validationMessage, undefined);
    act(() => done().props.onClick());
    assert.equal(root.root.findByType(BottomSheet).props.open, false);
    act(() => root.root.findByType(ConfirmView).props.onEdit());
    act(() => root.root.findByType(CandidateFieldsForm).props.onRemoveItem(0));
    act(() => done().props.onClick());
    assert.equal(root.root.findByType(BottomSheet).props.open, true);
    assert.equal(selectors.at(-1), "[data-add-item]", "missing items focus the add-item control, not an unrelated recent counterparty");
    assert.equal(writes, 0, "editing alone never confirms a transaction");
  } finally { await act(async () => root?.unmount()); router.dispose(); api.defaults.adapter = previous; useCandidateStore.getState().setCandidate(null); localStorage.clear(); }
});

test("confirmation fits known totals but preserves unknown total instead of inventing zero", async () => {
  const candidate = { counterparty: "상회", items: [], total: 100000000 as number | null, credit: true, dateLabel: "오늘", utterance: "가상 문장" };
  let root!: ReactTestRenderer;
  const view = () => <ConfirmView candidate={candidate} onBack={noop} onAnswer={noop} onCreditChange={noop} onEditItem={noop} onEdit={noop} onDiscard={noop} onConfirm={noop} />;
  try {
    await act(async () => { root = create(view()); });
    assert.equal(root.root.findByType(AmountText).props.value, 100000000);
    candidate.total = null;
    await act(async () => root.update(view()));
    assert.equal(root.root.findAllByType(AmountText).length, 0);
    assert.doesNotMatch(JSON.stringify(root.toJSON()), /0원/);
  } finally { await act(async () => root?.unmount()); }
});
