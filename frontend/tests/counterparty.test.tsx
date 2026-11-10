import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { api } from "../src/api/client";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { RecentCounterpartyPicker } from "../src/components/RecentCounterpartyPicker";
import { CandidateFieldsForm } from "../src/components/CandidateFieldsForm";
import { ClarificationCard } from "../src/components/ClarificationCard";
import { ConfirmPage } from "../src/pages/ConfirmPage";
import { ConfirmView } from "../src/ui/ConfirmView";
import { createCandidateDraft } from "../src/utils/candidateDraft";
import type { LedgerList } from "../src/types/api";
import { snapshot, noop, until } from "./review-fixture";

const entries: LedgerList["entries"] = [
  ...Array.from({ length: 7 }, (_, index) => ({ ...snapshot, entry_id: `entry-${index}`, status: "confirmed",
    counterparty: index === 6 ? " 최근상사 " : `거래처${index}`, created_at: `2026-10-${String(index + 1).padStart(2, "0")}T00:00:00Z`, kst_date: "2026-10-01" })),
  { ...snapshot, entry_id: "duplicate", status: "confirmed", counterparty: "거래처5", created_at: "2026-10-05T01:00:00Z", kst_date: "2026-10-05" },
  { ...snapshot, entry_id: "pending", status: "candidate", counterparty: "미확정", created_at: "2026-10-16T00:00:00Z", kst_date: "2026-10-16" },
  { ...snapshot, entry_id: "blank", status: "confirmed", counterparty: " ", created_at: "2026-10-16T00:00:00Z", kst_date: "2026-10-16" },
];

test("recent names are confirmed, trimmed, unique, newest first, and never selected automatically", async () => {
  const previous = api.defaults.adapter;
  let root!: ReactTestRenderer;
  const chosen: string[] = [];
  useAuthStore.getState().setCookieSession("owner");
  api.defaults.adapter = async (config) => {
    if (config.url?.endsWith("/unpaid")) return { config, status: 200, statusText: "OK", headers: {}, data: { counterparties: [] } };
    assert.match(config.params.month, /^\d{4}-\d{2}$/);
    return { config, status: 200, statusText: "OK", headers: {}, data: { entries, daily_credit: [] } };
  };
  try {
    await act(async () => { root = create(<RecentCounterpartyPicker value="거래처4" onSelect={(name) => chosen.push(name)} />); });
    const buttons = root.root.findAllByType("button");
    assert.deepEqual(buttons.map((button) => button.children.join("")), ["최근상사", "거래처5", "거래처4", "거래처3", "거래처2"]);
    assert.equal(buttons[2].props["aria-pressed"], true);
    assert.deepEqual(chosen, []);
    act(() => buttons[0].props.onClick());
    assert.deepEqual(chosen, ["최근상사"]);
    const stored = Array.from({ length: localStorage.length }, (_, index) => localStorage.getItem(localStorage.key(index)!));
    assert.doesNotMatch(JSON.stringify(stored), /최근상사|거래처[0-6]/);
  } finally { act(() => root?.unmount()); api.defaults.adapter = previous; }
});

test("recent lookup failure keeps direct input available and retries without changing the draft", async () => {
  const previous = api.defaults.adapter;
  let root!: ReactTestRenderer;
  let calls = 0;
  let failing = true;
  const changes: unknown[] = [];
  useAuthStore.getState().setCookieSession("owner");
  api.defaults.adapter = async (config) => {
    calls++;
    if (failing) throw new Error("offline");
    return { config, status: 200, statusText: "OK", headers: {}, data: { entries: [], daily_credit: [], counterparties: [] } };
  };
  try {
    await act(async () => { root = create(<CandidateFieldsForm draft={createCandidateDraft(snapshot, snapshot.entry_id)}
      onField={(...args) => changes.push(args)} onItem={noop} onAddItem={noop} onRemoveItem={noop} />); });
    assert.match(JSON.stringify(root.toJSON()), /최근 거래처를 불러오지 못했어요/);
    const name = root.root.findAllByType("input").find((input) => input.props.value === snapshot.counterparty)!;
    act(() => name.props.onChange({ target: { value: "직접 입력한 거래처" } }));
    assert.deepEqual(changes, [["counterparty", "직접 입력한 거래처"]]);
    const firstCalls = calls;
    failing = false;
    await act(async () => root.root.findAllByType("button").find((button) => button.children.includes("최근 거래처 다시 불러오기"))!.props.onClick());
    assert.match(JSON.stringify(root.toJSON()), /최근 기록한 거래처가 없어요/);
    assert.equal(calls, firstCalls * 2); assert.equal(changes.length, 1);
  } finally { act(() => root?.unmount()); api.defaults.adapter = previous; }
});

for (const nextOwner of ["new-owner", "old-owner"]) {
test(`session replacement (${nextOwner}) reloads names and rejects a late response`, async () => {
  const previous = api.defaults.adapter;
  let root!: ReactTestRenderer;
  let release!: () => void;
  let calls = 0;
  useAuthStore.getState().setCookieSession("old-owner");
  api.defaults.adapter = (config) => {
    if (config.url?.endsWith("/unpaid")) return Promise.resolve({ config, status: 200, statusText: "OK", headers: {}, data: { counterparties: [] } });
    const response = (name: string) => ({ config, status: 200, statusText: "OK", headers: {},
      data: { entries: [{ ...entries[0], counterparty: name }], daily_credit: [] } });
    if (++calls === 1) return new Promise((resolve) => { release = () => resolve(response("이전 계정 거래처")); });
    return Promise.resolve(response("새 계정 거래처"));
  };
  try {
    await act(async () => { root = create(<RecentCounterpartyPicker value="" onSelect={noop} />); });
    assert.match(JSON.stringify(root.toJSON()), /불러오고 있어요/);
    await act(async () => useAuthStore.getState().setCookieSession(nextOwner));
    assert.match(JSON.stringify(root.toJSON()), /새 계정 거래처/);
    await act(async () => release());
    assert.doesNotMatch(JSON.stringify(root.toJSON()), /이전 계정 거래처/);
    await act(async () => useAuthStore.getState().setCookieSession(null));
    assert.equal(root.toJSON(), null);
  } finally { act(() => root?.unmount()); api.defaults.adapter = previous; }
});
}

test("clarification and confirmation use the same recent-name selection without auto-confirming", async () => {
  const previous = api.defaults.adapter;
  let root!: ReactTestRenderer;
  const changed: string[] = [];
  let writes = 0;
  localStorage.clear();
  useAuthStore.getState().setCookieSession("owner");
  useCandidateStore.getState().setCandidate(snapshot);
  api.defaults.adapter = async (config) => {
    if (config.method !== "get") writes++;
    return { config, status: 200, statusText: "OK", headers: {}, data: { entries, daily_credit: [], counterparties: [] } };
  };
  const router = createMemoryRouter([{ path: "/record/confirm", element: <ConfirmPage /> }], { initialEntries: ["/record/confirm"] });
  try {
    const missing = { ...createCandidateDraft(snapshot, snapshot.entry_id), counterparty: "" };
    await act(async () => { root = create(<ClarificationCard draft={missing} busy={false} initialField="counterparty"
      onChange={(draft) => changed.push(draft.counterparty)} onFinish={noop} onClose={noop} />); });
    act(() => root.root.findAllByType("button").find((button) => button.children.includes("최근상사"))!.props.onClick());
    assert.deepEqual(changed, ["최근상사"]);
    act(() => root.unmount());
    await act(async () => { root = create(<RouterProvider router={router} />); });
    assert.equal(root.root.findAllByType(RecentCounterpartyPicker).length, 0, "closed editor must not fetch recent names");
    await act(async () => root.root.findByType(ConfirmView).props.onEdit());
    await act(async () => { await until(() => root.root.findAllByType("button").some((button) => button.children.includes("최근상사"))); });
    act(() => root.root.findAllByType("button").find((button) => button.children.includes("최근상사"))!.props.onClick());
    assert.equal(root.root.findByType(CandidateFieldsForm).props.draft.counterparty, "최근상사");
    assert.equal(useCandidateStore.getState().candidate?.counterparty, snapshot.counterparty);
    assert.equal(writes, 0, "choosing a name must never write a ledger entry");
  } finally { act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous; useCandidateStore.getState().setCandidate(null); localStorage.clear(); }
});
