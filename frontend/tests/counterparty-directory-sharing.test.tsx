import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { api } from "../src/api/client";
import { useAuthStore } from "../src/stores/appStore";
import { useRecentCounterparties } from "../src/utils/useRecentCounterparties";
import { counterpartyWindow } from "../src/utils/counterpartyDirectory";
import { kstToday } from "../src/utils/useLedgerData";
import "./review-fixture";

test("simultaneous pickers share pending reads but reopening after completion fetches fresh names", async () => {
  const previous = api.defaults.adapter;
  useAuthStore.getState().setCookieSession("shared-directory-owner");
  const results: Record<string, ReturnType<typeof useRecentCounterparties>> = {};
  const release: Array<() => void> = [];
  let calls = 0, root!: ReactTestRenderer;
  api.defaults.adapter = (config) => {
    calls++;
    return new Promise((resolve) => release.push(() => resolve({ config, status: 200, statusText: "OK", headers: {},
      data: config.url?.endsWith("/unpaid") ? { counterparties: [{ counterparty: "공유 상회", unpaid: 100 }] } : { entries: [] } })));
  };
  function Picker({ id }: { id: string }) { results[id] = useRecentCounterparties(); return null; }
  const expected = counterpartyWindow(kstToday()).months.length + 1;
  try {
    await act(async () => { root = create(<><Picker id="suggestion" /><Picker id="recent" /></>); });
    assert.equal(calls, expected, "one batch serves concurrent consumers in the same session");
    await act(async () => release.splice(0).forEach((finish) => finish()));
    assert.deepEqual(results.suggestion.names, ["공유 상회"]);
    assert.deepEqual(results.recent.names, results.suggestion.names);
    await act(async () => root.update(<><Picker id="suggestion" /><Picker id="recent" /><Picker id="new" /></>));
    assert.equal(calls, expected * 2, "completed results are not cached across later edits");
    await act(async () => release.splice(0).forEach((finish) => finish()));
  } finally { act(() => root?.unmount()); api.defaults.adapter = previous; }
});

for (const nextOwner of ["shared-session-owner", "another-owner"]) {
test(`session replacement (${nextOwner}) starts fresh reads and rejects the previous batch`, async () => {
  const previous = api.defaults.adapter;
  useAuthStore.getState().setCookieSession("shared-session-owner");
  let latest!: ReturnType<typeof useRecentCounterparties>, root!: ReactTestRenderer;
  const release: Array<() => void> = [];
  const expected = counterpartyWindow(kstToday()).months.length + 1;
  let calls = 0;
  api.defaults.adapter = (config) => {
    const old = ++calls <= expected;
    return new Promise((resolve) => release.push(() => resolve({ config, status: 200, statusText: "OK", headers: {},
      data: config.url?.endsWith("/unpaid") ? { counterparties: [{ counterparty: old ? "이전 상회" : "새 상회", unpaid: 100 }] } : { entries: [] } })));
  };
  function Picker({ primary = false }: { primary?: boolean }) {
    const result = useRecentCounterparties();
    if (primary) latest = result;
    return null;
  }
  try {
    await act(async () => { root = create(<><Picker primary /></>); });
    await act(async () => useAuthStore.getState().setCookieSession(nextOwner));
    assert.equal(calls, expected * 2);
    await act(async () => release.splice(0, expected).forEach((finish) => finish()));
    assert.deepEqual(latest.names, []);
    assert.equal(latest.loading, true);
    await act(async () => root.update(<><Picker primary /><Picker /></>));
    assert.equal(calls, expected * 2, "old completion must not clear the new session's pending batch");
    await act(async () => release.splice(0).forEach((finish) => finish()));
    assert.deepEqual(latest.names, ["새 상회"]);
  } finally { act(() => root?.unmount()); api.defaults.adapter = previous; }
});
}

test("a failed shared batch is released so retry can read a complete directory", async () => {
  const previous = api.defaults.adapter;
  useAuthStore.getState().setCookieSession("retry-directory-owner");
  let latest!: ReturnType<typeof useRecentCounterparties>, root!: ReactTestRenderer;
  let failed = true, calls = 0;
  api.defaults.adapter = async (config) => {
    calls++;
    if (failed && config.params?.month) throw new Error("synthetic offline");
    return { config, status: 200, statusText: "OK", headers: {},
      data: config.url?.endsWith("/unpaid") ? { counterparties: [{ counterparty: "직접 선택 상회", unpaid: 100 }] } : { entries: [] } };
  };
  function Picker() { latest = useRecentCounterparties(); return null; }
  try {
    await act(async () => { root = create(<Picker />); });
    assert.equal(latest.failed, true);
    assert.deepEqual(latest.names, ["직접 선택 상회"]);
    const firstCalls = calls;
    failed = false;
    await act(async () => latest.retry());
    assert.equal(latest.failed, false);
    assert.equal(calls, firstCalls * 2);
  } finally { act(() => root?.unmount()); api.defaults.adapter = previous; }
});
