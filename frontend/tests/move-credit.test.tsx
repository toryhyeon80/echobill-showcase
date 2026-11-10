import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { AxiosError, type InternalAxiosRequestConfig } from "axios";
import { api } from "../src/api/client";
import { MoveCreditDialog } from "../src/components/MoveCreditDialog";
import { MoveCreditView } from "../src/ui/MoveCreditView";
import { useAuthStore, useToastStore } from "../src/stores/appStore";
import { ledgerEntryView, MOVED_LABEL, settlementView } from "../src/utils/viewModels";
import { until } from "./review-fixture";

const INPUT = { counterparty: "한솥밥식당", amount: 120000, when: "last_month" as const };

test("a moved total reads as 기존 외상 in the ledger and the counterparty detail", () => {
  const entry = { entry_id: "e1", status: "confirmed" as const, counterparty: "한솥밥식당", items: [], total_amount: 120000,
    credit: true, created_at: "2026-09-15T00:00:00Z", moved: true };
  assert.equal(ledgerEntryView(entry).summary, MOVED_LABEL);
  const view = settlementView({ counterparty: "한솥밥식당", credit_total: 120000, paid_total: 0, unpaid: 120000, owed_count: 1,
    oldest_days: 39, payments: [], entries: [{ entry_id: "e1", kst_date: "2026-09-15", total_amount: 120000, allocated: 0, remaining: 120000, moved: true }] });
  assert.equal(view.entries[0].dateLabel, `2026-09-15 무렵 · ${MOVED_LABEL}`);
});

test("saving posts the input once with an idempotency key, reloads and tells the owner", async () => {
  const previous = api.defaults.adapter;
  const seen: InternalAxiosRequestConfig[] = [];
  api.defaults.adapter = async (config) => {
    seen.push(config);
    return { data: { entry_id: "e1", moved: true }, status: 200, statusText: "OK", headers: {}, config };
  };
  useAuthStore.getState().setCookieSession("move-owner");
  let moved = 0, closed = 0, root!: ReactTestRenderer;
  try {
    await act(async () => { root = create(<MoveCreditDialog onClose={() => closed++} onMoved={() => moved++} />); });
    await act(async () => { root.root.findByType(MoveCreditView).props.onSubmit(INPUT); });
    await act(async () => { await until(() => closed === 1); });
    assert.equal(seen.length, 1);
    assert.equal(seen[0].url, "/api/v1/ledger/moved-credit");
    assert.deepEqual(JSON.parse(seen[0].data as string), INPUT);
    assert.ok(seen[0].headers?.["X-Idempotency-Key"]);
    assert.equal(moved, 1); assert.equal(closed, 1);
    assert.match(useToastStore.getState().message ?? "", /한솥밥식당 120,000원을 옮겼어요/);
  } finally { await act(async () => root?.unmount()); api.defaults.adapter = previous; }
});

test("a rejected move stays open with the server's message and saves nothing", async () => {
  const previous = api.defaults.adapter;
  api.defaults.adapter = async (config) => {
    throw new AxiosError("bad", undefined, config, undefined, { data: { error_code: "AMOUNT_OUT_OF_RANGE",
      message: "금액이 허용 범위를 벗어났습니다.", problem_fields: ["amount"] }, status: 400, statusText: "Bad Request", headers: {}, config });
  };
  useAuthStore.getState().setCookieSession("move-owner-2");
  let moved = 0, closed = 0, root!: ReactTestRenderer;
  try {
    await act(async () => { root = create(<MoveCreditDialog onClose={() => closed++} onMoved={() => moved++} />); });
    await act(async () => { root.root.findByType(MoveCreditView).props.onSubmit(INPUT); });
    await act(async () => { await until(() => !!root.root.findByType(MoveCreditView).props.error); });
    assert.equal(moved, 0); assert.equal(closed, 0);
    assert.ok(root.root.findByType(MoveCreditView).props.error);
  } finally { await act(async () => root?.unmount()); api.defaults.adapter = previous; }
});

for (const change of ["same-owner", "other-owner", "unmount", "failed-after-session"] as const) {
  test(`a moved credit response cannot affect a replaced screen or login: ${change}`, async () => {
    const previous = api.defaults.adapter;
    useAuthStore.getState().setCookieSession("move-original");
    useToastStore.getState().clear();
    let release = () => {};
    let moved = 0, closed = 0, calls = 0;
    api.defaults.adapter = (config) => {
      calls++;
      return new Promise((resolve, reject) => { release = () => change === "failed-after-session"
        ? reject(new AxiosError("late failure")) : resolve({ config, status: 200, statusText: "OK", headers: {}, data: { moved: true } }); });
    };
    let root!: ReactTestRenderer;
    try {
      await act(async () => { root = create(<MoveCreditDialog onClose={() => closed++} onMoved={() => moved++} />); });
      await act(async () => { root.root.findByType(MoveCreditView).props.onSubmit(INPUT); await until(() => calls === 1); });
      await act(async () => {
        if (change === "unmount") root.unmount();
        else useAuthStore.getState().setCookieSession(change === "other-owner" ? "move-other" : "move-original");
      });
      await act(async () => { release(); await new Promise((resolve) => setTimeout(resolve, 0)); });
      assert.equal(moved, 0);
      assert.equal(closed, change === "unmount" ? 0 : 1, "session changes close only the old form");
      assert.equal(useToastStore.getState().message, null);
      if (change !== "unmount") assert.equal(root.root.findAllByType(MoveCreditView).length, 0);
    } finally { release(); act(() => root?.unmount()); api.defaults.adapter = previous; }
  });
}

test("two immediate submit gestures send only one move request", async () => {
  const previous = api.defaults.adapter;
  useAuthStore.getState().setCookieSession("double-move-owner");
  let calls = 0, release = () => {}, moved = 0;
  api.defaults.adapter = (config) => {
    calls++;
    return new Promise((resolve) => { release = () => resolve({ config, status: 200, statusText: "OK", headers: {}, data: {} }); });
  };
  let root!: ReactTestRenderer;
  try {
    await act(async () => { root = create(<MoveCreditDialog onClose={() => {}} onMoved={() => moved++} />); });
    await act(async () => {
      const submit = root.root.findByType(MoveCreditView).props.onSubmit;
      submit(INPUT); submit(INPUT);
      await until(() => calls > 0);
    });
    assert.equal(calls, 1);
    await act(async () => { release(); await until(() => moved === 1); });
  } finally { release(); act(() => root?.unmount()); api.defaults.adapter = previous; }
});
