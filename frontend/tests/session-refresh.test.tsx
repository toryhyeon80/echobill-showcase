import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { api } from "../src/api/client";
import { useAuthStore } from "../src/stores/appStore";
import { useLedgerData } from "../src/utils/useLedgerData";
import { snapshot } from "./review-fixture";

type LedgerRead = ReturnType<typeof useLedgerData>;

test("replacing the same owner's session hides old balances and rereads both sources", async () => {
  const previous = api.defaults.adapter;
  let root!: ReactTestRenderer, latest!: LedgerRead;
  let round = 1, reads = 0;
  const release: Array<() => void> = [];
  useAuthStore.getState().setCookieSession("same-owner");
  api.defaults.adapter = (config) => {
    reads++;
    const name = round === 1 ? "이전잔액상사" : "새잔액상사";
    const data = config.url?.endsWith("/unpaid") ? { total_unpaid: round * 10000, counterparties: [{ counterparty: name, unpaid: round * 10000 }] }
      : { entries: [{ ...snapshot, status: "confirmed", counterparty: name }], daily_credit: [] };
    const response = { config, data, status: 200, statusText: "OK", headers: {} };
    return round === 1 ? Promise.resolve(response) : new Promise((resolve) => release.push(() => resolve(response)));
  };
  function Probe() { latest = useLedgerData("2026-10"); return <p>{latest.unpaid?.total_unpaid ?? "확인 중"}</p>; }
  try {
    await act(async () => { root = create(<Probe />); });
    assert.equal(latest.unpaid?.total_unpaid, 10000);
    round = 2;
    await act(async () => useAuthStore.getState().setCookieSession("same-owner"));
    assert.equal(reads, 4, "a new cookie session must reread ledger and balances even for the same ID");
    assert.equal(latest.unpaid, null); assert.equal(latest.ledger, null); assert.equal(latest.busy, true);
    await act(async () => release.forEach((finish) => finish()));
    assert.equal(latest.unpaid?.total_unpaid, 20000); assert.equal(latest.busy, false);
    assert.equal(latest.ledger?.entries[0].counterparty, "새잔액상사");
  } finally { act(() => root?.unmount()); api.defaults.adapter = previous; }
});

test("a rejected new session cannot fall back to balances cached by the previous session", async () => {
  const previous = api.defaults.adapter;
  let root!: ReactTestRenderer, latest!: LedgerRead;
  let failing = false;
  useAuthStore.getState().setCookieSession("owner");
  api.defaults.adapter = async (config) => {
    if (failing) throw new Error("offline");
    return { config, status: 200, statusText: "OK", headers: {}, data: config.url?.endsWith("/unpaid")
      ? { total_unpaid: 99999, counterparties: [] } : { entries: [], daily_credit: [] } };
  };
  function Probe() { latest = useLedgerData("2026-10"); return null; }
  try {
    await act(async () => { root = create(<Probe />); });
    failing = true;
    await act(async () => useAuthStore.getState().setCookieSession("owner"));
    assert.equal(latest.unpaid, null); assert.equal(latest.ledger, null);
    assert.ok(latest.error); assert.equal(latest.busy, false);
  } finally { act(() => root?.unmount()); api.defaults.adapter = previous; }
});

test("an ordinary same-session refresh may retain known balances alongside an explicit error", async () => {
  const previous = api.defaults.adapter;
  let root!: ReactTestRenderer, latest!: LedgerRead;
  let failing = false;
  useAuthStore.getState().setCookieSession("owner");
  api.defaults.adapter = async (config) => {
    if (failing) throw new Error("offline");
    return { config, status: 200, statusText: "OK", headers: {}, data: config.url?.endsWith("/unpaid")
      ? { total_unpaid: 15000, counterparties: [] } : { entries: [], daily_credit: [] } };
  };
  function Probe() { latest = useLedgerData("2026-10"); return null; }
  try {
    await act(async () => { root = create(<Probe />); });
    failing = true; await act(async () => latest.reload());
    assert.equal(latest.unpaid?.total_unpaid, 15000); assert.ok(latest.error); assert.equal(latest.busy, false);
  } finally { act(() => root?.unmount()); api.defaults.adapter = previous; }
});
