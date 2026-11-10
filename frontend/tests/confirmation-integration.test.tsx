import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { api } from "../src/api/client";
import { useAuthStore, useCandidateStore, useToastStore } from "../src/stores/appStore";
import { ConfirmPage } from "../src/pages/ConfirmPage";
import { ConfirmView } from "../src/ui/ConfirmView";
import { AmountText } from "../src/ui/AmountText";
import { BottomSheet } from "../src/ui/BottomSheet";
import { CandidateFieldsForm } from "../src/components/CandidateFieldsForm";
import { saveCounterpartySuggestions } from "../src/utils/counterpartySuggestionSettings";
import { snapshot, until } from "./review-fixture";

test("suggestion, field errors and large totals coexist until one explicit corrected confirmation", async () => {
  const previous = api.defaults.adapter;
  localStorage.clear();
  useAuthStore.getState().setCookieSession("integrated-confirmation-owner");
  saveCounterpartySuggestions(true);
  const candidate = { ...snapshot, counterparty: "카나상회", total_amount: 100000000,
    items: [{ ...snapshot.items[0], qty: null, amount: 100000000 }] };
  useCandidateStore.getState().setCandidate(candidate);
  const writes: Array<{ action: string; corrected: typeof candidate }> = [];
  api.defaults.adapter = async (config) => {
    if (config.method !== "get") writes.push(JSON.parse(config.data));
    const data = config.method !== "get" ? { ...candidate, status: "confirmed" }
      : config.url?.endsWith("/unpaid") ? { counterparties: [{ counterparty: "가나상회", unpaid: 100 }] }
      : { entries: [] };
    return { config, status: 200, statusText: "OK", headers: {}, data };
  };
  const router = createMemoryRouter([{ path: "/confirm", element: <ConfirmPage /> }, { path: "/ledger", element: <p>장부</p> }], { initialEntries: ["/confirm"] });
  let root!: ReactTestRenderer;
  try {
    await act(async () => { root = create(<RouterProvider router={router} />); });
    assert.equal(root.root.findByType(AmountText).props.value, 100000000);
    assert.match(root.root.findByType(ConfirmView).props.validationMessage, /수량을 확인/);
    assert.equal(root.root.findByType(ConfirmView).props.disabled, true);
    act(() => root.root.findAllByType("button").find((button) => button.props["aria-label"] === "가나상회 선택")!.props.onClick());
    assert.equal(root.root.findByType(ConfirmView).props.candidate.counterparty, "가나상회");
    assert.match(root.root.findByType(ConfirmView).props.validationMessage, /수량을 확인/);
    await act(async () => root.root.findByType(ConfirmView).props.onConfirm());
    assert.equal(writes.length, 0, "a suggestion cannot bypass invalid quantity or confirm by itself");
    act(() => root.root.findByType(ConfirmView).props.onEdit());
    assert.equal(root.root.findByType(ConfirmView).props.validationMessage, undefined, "the open editor owns the error announcement");
    act(() => root.root.findByType(CandidateFieldsForm).props.onItem(0, "qty", "2"));
    act(() => root.root.findAllByType("button").find((button) => button.children.some((child) => typeof child === "string" && child.trim() === "수정 완료"))!.props.onClick());
    assert.equal(root.root.findByType(BottomSheet).props.open, false);
    assert.equal(root.root.findByType(ConfirmView).props.disabled, false);
    assert.equal(root.root.findByType(AmountText).props.value, 100000000);
    assert.equal(writes.length, 0);
    await act(async () => {
      root.root.findByType(ConfirmView).props.onConfirm();
      await until(() => router.state.location.pathname === "/ledger");
    });
    assert.equal(writes.length, 1);
    assert.equal(writes[0].action, "correct");
    assert.equal(writes[0].corrected.counterparty, "가나상회");
    assert.equal(writes[0].corrected.items[0].qty, 2);
    assert.equal(writes[0].corrected.total_amount, 100000000);
  } finally {
    act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous;
    saveCounterpartySuggestions(false); useCandidateStore.getState().setCandidate(null);
    useToastStore.getState().clear(); localStorage.clear();
  }
});
