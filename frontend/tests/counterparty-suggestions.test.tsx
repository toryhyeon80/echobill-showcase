import { test } from "node:test";
import assert from "node:assert/strict";
import { useState } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { api } from "../src/api/client";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { CounterpartySuggestion } from "../src/components/CounterpartySuggestion";
import { ConfirmPage } from "../src/pages/ConfirmPage";
import { ConfirmView } from "../src/ui/ConfirmView";
import { counterpartySimilarity, suggestCounterparties } from "../src/utils/counterpartySuggestions";
import { getSuggestionCounts, saveCounterpartySuggestions } from "../src/utils/counterpartySuggestionSettings";
import { counterpartyWindow, loadCounterpartyDirectory } from "../src/utils/counterpartyDirectory";
import { snapshot, noop, until } from "./review-fixture";
import { kstToday } from "../src/utils/useLedgerData";

test("90-day window crosses year/leap boundaries without host-timezone arithmetic", () => {
  assert.deepEqual(counterpartyWindow("2026-01-02"), { first: "2025-10-05", today: "2026-01-02", months: ["2025-10", "2025-11", "2025-12", "2026-01"] });
  assert.equal(counterpartyWindow("2024-03-01").first, "2023-12-03");
});

test("directory includes cutoff day and older outstanding debt, excludes future/pending, and deduplicates", async () => {
  const previous = api.defaults.adapter;
  const requests: string[] = [];
  api.defaults.adapter = async (config) => {
    requests.push(config.url?.endsWith("/unpaid") ? "unpaid" : config.params.month);
    const rows = [
      { name: "최근상사", date: "2026-10-19", status: "confirmed" },
      { name: " 경계상사 ", date: "2026-07-22", status: "confirmed" },
      { name: "기간밖", date: "2026-07-21", status: "confirmed" },
      { name: "미래", date: "2026-10-20", status: "confirmed" },
      { name: "미확정", date: "2026-10-19", status: "candidate" },
    ];
    const data = config.url?.endsWith("/unpaid") ? { counterparties: [{ counterparty: "오래된외상", unpaid: 5000 }, { counterparty: "최근상사", unpaid: 1000 }, { counterparty: "정산됨", unpaid: 0 }] }
      : { entries: rows.filter((row) => row.date.startsWith(config.params.month)).map((row) => ({ ...snapshot, counterparty: row.name, status: row.status, created_at: `${row.date}T00:00:00Z`, kst_date: row.date })) };
    return { config, status: 200, statusText: "OK", headers: {}, data };
  };
  try {
    assert.deepEqual(await loadCounterpartyDirectory("2026-10-19"), { names: ["최근상사", "경계상사", "오래된외상"], failed: false });
    assert.deepEqual(requests.sort(), ["2026-07", "2026-08", "2026-09", "2026-10", "unpaid"]);
    api.defaults.adapter = async (config) => {
      if (!config.url?.endsWith("/unpaid")) throw new Error("offline");
      return { config, status: 200, statusText: "OK", headers: {}, data: { counterparties: [{ counterparty: "받을돈상사", unpaid: 5 }] } };
    };
    assert.deepEqual(await loadCounterpartyDirectory("2026-10-19"), { names: ["받을돈상사"], failed: true });
  } finally { api.defaults.adapter = previous; }
});

test("provisional matcher handles NFC and sound-alike consonants, and abstains on exact/blank/weak/too many", () => {
  assert.equal(counterpartySimilarity("가나상회", "가나상회".normalize("NFD")), 1);
  assert(counterpartySimilarity("카나상회", "가나상회") > counterpartySimilarity("하나상회", "가나상회"));
  assert.deepEqual(suggestCounterparties("카나상회", ["가나상회", "먼나라식품"]), ["가나상회"]);
  assert.deepEqual(suggestCounterparties("가나 상회", ["가나상회", "강화상회"]), []);
  assert.deepEqual(suggestCounterparties("", ["가나상회"]), []);
  assert.deepEqual(suggestCounterparties("다른식당", ["가나상회"]), []);
  const names = ["카다상회", "까다상회", "가타상회"];
  assert.deepEqual(suggestCounterparties("가다상회", names), names);
  assert.deepEqual(suggestCounterparties("가다상회", [...names, "카타상회"]), []);
  assert.deepEqual(suggestCounterparties("가".repeat(301), ["나".repeat(301)]), []);
});

test("suggestions include a score gap of exactly 0.04 despite floating-point rounding", () => {
  const input = "강남동산길가나다라마";
  const best = "캉남동산길가나다라마";
  const boundary = "캉남동산길거나다라마";
  const outside = "캉남동산길거너다라마";
  // Synthetic names reproduce scores 0.986 and 0.946, whose subtraction exceeds 0.04 in JS.
  assert.deepEqual(suggestCounterparties(input, [outside, boundary, best]), [best, boundary]);
});

async function fixture() {
  localStorage.clear();
  useAuthStore.getState().setCookieSession("suggestion-owner");
  const previous = api.defaults.adapter;
  let reads = 0, writes = 0;
  let partial = false;
  const bodies: unknown[] = [];
  api.defaults.adapter = async (config) => {
    if (config.method !== "get") {
      writes++; bodies.push(JSON.parse(config.data));
      return { config, status: 200, statusText: "OK", headers: {}, data: { ...snapshot, status: "confirmed" } };
    }
    reads++;
    if (partial && config.params?.month) throw new Error("offline");
    const data = config.url?.endsWith("/unpaid") ? { counterparties: [{ counterparty: "가나상회", unpaid: 20 }] }
      : { entries: [{ ...snapshot, counterparty: "가나상회", status: "confirmed", kst_date: kstToday(), created_at: `${kstToday()}T00:00:00Z` }] };
    return { config, status: 200, statusText: "OK", headers: {}, data };
  };
  return { bodies, reads: () => reads, writes: () => writes, partial: () => { partial = true; }, close: () => { api.defaults.adapter = previous; saveCounterpartySuggestions(false); useCandidateStore.getState().setCandidate(null); localStorage.clear(); } };
}

test("off by default; enabling shows suggestions without writes; selection and one undo affect only name", async () => {
  const f = await fixture();
  let root!: ReactTestRenderer;
  function Harness() {
    const [value, setValue] = useState("카나상회");
    return <><p data-value={value} /><CounterpartySuggestion value={value} onSelect={setValue} onEdit={noop} /></>;
  }
  try {
    await act(async () => { root = create(<Harness />); });
    assert.equal(f.reads(), 0);
    await act(async () => saveCounterpartySuggestions(true));
    assert.match(JSON.stringify(root.toJSON()), /혹시 가나상회인가요/);
    const suggestionRow = root.root.findByProps({ "aria-label": "거래처 이름 제안" });
    assert.match(suggestionRow.props.className, /mt-space-3 mb-space-4/);
    assert.doesNotMatch(JSON.stringify(root.toJSON()), /border-dashed|거래처를 확인해 주세요/);
    assert.equal(root.root.findAllByType("button").find(button => button.props["aria-label"] === "가나상회 선택")!.children.join(""), "선택");
    assert.deepEqual(getSuggestionCounts(), { shown: 1, selected: 0, undone: 0 });
    act(() => root.root.findAllByType("button").find((button) => button.props["aria-label"] === "가나상회 선택")!.props.onClick());
    assert.equal(root.root.findByProps({ "data-value": "가나상회" }).type, "p");
    const selectedRow = root.root.findByProps({ role: "status" });
    assert.match(selectedRow.props.className, /mt-space-3 mb-space-4/);
    assert.match(JSON.stringify(selectedRow.findByType("p").children.join("")), /가나상회로 바꿨어요/);
    act(() => root.root.findAllByType("button").find((button) => button.children.includes("되돌리기"))!.props.onClick());
    assert.equal(root.root.findByProps({ "data-value": "카나상회" }).type, "p");
    assert.equal(root.root.findAllByType("button").filter((button) => button.children.includes("되돌리기")).length, 0);
    assert.deepEqual(getSuggestionCounts(), { shown: 1, selected: 1, undone: 1 });
    assert.equal(f.writes(), 0);
    assert.doesNotMatch(JSON.stringify(Array.from({ length: localStorage.length }, (_, i) => localStorage.getItem(localStorage.key(i)!))), /가나|카나/);
    await act(async () => saveCounterpartySuggestions(false));
    assert.equal(root.root.findAllByType("button").length, 0);
  } finally { act(() => root?.unmount()); f.close(); }
});

test("partial directory must not suggest from an incomplete list", async () => {
  const f = await fixture(); let root!: ReactTestRenderer;
  f.partial(); saveCounterpartySuggestions(true);
  try {
    await act(async () => { root = create(<CounterpartySuggestion value="카나상회" onSelect={noop} onEdit={noop} />); });
    assert.equal(root.toJSON(), null);
  } finally { act(() => root?.unmount()); f.close(); }
});

test("manual edits revoke undo and replacing even the same owner's session clears the counts", async () => {
  const f = await fixture(); let root!: ReactTestRenderer;
  saveCounterpartySuggestions(true);
  let edit!: (value: string) => void;
  function Harness() {
    const [value, setValue] = useState("카나상회"); edit = setValue;
    return <CounterpartySuggestion value={value} onSelect={setValue} onEdit={noop} />;
  }
  try {
    await act(async () => { root = create(<Harness />); });
    act(() => root.root.findAllByType("button").find((button) => button.props["aria-label"] === "가나상회 선택")!.props.onClick());
    assert.match(JSON.stringify(root.toJSON()), /되돌리기/);
    act(() => edit("직접고친상회"));
    assert.equal(root.toJSON(), null);
    act(() => edit("가나상회"));
    assert.equal(root.toJSON(), null, "typing the selected name again cannot revive an old undo");
    await act(async () => useAuthStore.getState().setCookieSession("suggestion-owner"));
    assert.deepEqual(getSuggestionCounts(), { shown: 0, selected: 0, undone: 0 });
    await act(async () => useAuthStore.getState().setCookieSession(null));
    assert.equal(root.toJSON(), null);
  } finally { act(() => root?.unmount()); f.close(); }
});

test("suggestion changes feed the existing corrected feedback only after explicit confirmation", async () => {
  const f = await fixture(); let root!: ReactTestRenderer;
  saveCounterpartySuggestions(true);
  useCandidateStore.getState().setCandidate({ ...snapshot, counterparty: "카나상회" });
  const router = createMemoryRouter([{ path: "/confirm", element: <ConfirmPage /> }, { path: "/ledger", element: <p>장부</p> }], { initialEntries: ["/confirm"] });
  try {
    await act(async () => { root = create(<RouterProvider router={router} />); });
    act(() => root.root.findAllByType("button").find((button) => button.props["aria-label"] === "가나상회 선택")!.props.onClick());
    assert.equal(f.writes(), 0);
    assert.equal(useCandidateStore.getState().candidate?.counterparty, "카나상회");
    await act(async () => {
      root.root.findByType(ConfirmView).props.onConfirm();
      await until(() => router.state.location.pathname === "/ledger");
    });
    assert.equal(f.writes(), 1);
    const body = f.bodies[0] as { action: string; corrected: typeof snapshot };
    assert.equal(body.action, "correct"); assert.equal(body.corrected.counterparty, "가나상회");
    assert.equal(body.corrected.total_amount, snapshot.total_amount); assert.equal(body.corrected.credit, snapshot.credit);
    assert.equal(router.state.location.pathname, "/ledger");
  } finally { act(() => root?.unmount()); router.dispose(); f.close(); }
});
