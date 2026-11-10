import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { LedgerPage } from "../src/pages/LedgerPage";
import { LedgerView } from "../src/ui/LedgerView";
import { PaymentSheet } from "../src/components/PaymentSheet";
import { api } from "../src/api/client";
import { useAuthStore } from "../src/stores/appStore";
import { until } from "./review-fixture";

test("a late payment cannot keep a new session locked or unlock its newer payment", async () => {
  localStorage.clear(); sessionStorage.clear();
  useAuthStore.getState().setCookieSession("payment-owner");
  const previous = api.defaults.adapter;
  const finish: Array<() => void> = [];
  api.defaults.adapter = (config) => {
    const response = (data: unknown) => ({ config, status: 200, statusText: "OK", headers: {}, data });
    if (config.method === "post") return new Promise((resolve) => finish.push(() => resolve(response({}))));
    return Promise.resolve(response(config.url?.endsWith("/unpaid") ? { total_unpaid: 10000,
      counterparties: [{ counterparty: "가상상회", credit_total: 10000, paid_total: 0, unpaid: 10000 }] }
      : { entries: [], daily_credit: [] }));
  };
  const router = createMemoryRouter([{ path: "/", element: <LedgerPage /> }]);
  let root!: ReactTestRenderer;
  const payment = () => root.root.findByType(PaymentSheet);
  const open = async (amount: string) => {
    act(() => root.root.findByType(LedgerView).props.onPayment());
    act(() => payment().props.onAmount(amount));
  };
  try {
    await act(async () => { root = create(<RouterProvider router={router} />); });
    await open("1000");
    await act(async () => { payment().props.onSubmit(); await until(() => finish.length === 1); });
    await act(async () => useAuthStore.getState().setCookieSession("payment-owner"));
    await open("2000");
    assert.equal(payment().props.busy, false, "the old request must not lock a new session");
    await act(async () => { payment().props.onSubmit(); await until(() => finish.length === 2); });
    await act(async () => finish[0]());
    assert.equal(payment().props.busy, true, "old finally must not unlock the current payment");
    assert.equal(payment().props.amount, "2000");
    await act(async () => finish[1]());
    assert.equal(root.root.findAllByType(PaymentSheet).length, 0);
  } finally {
    act(() => root?.unmount()); router.dispose(); finish.forEach((resolve) => resolve());
    api.defaults.adapter = previous; localStorage.clear(); sessionStorage.clear();
  }
});
