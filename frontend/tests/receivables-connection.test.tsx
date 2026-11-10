import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { renderToStaticMarkup } from "react-dom/server";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { api } from "../src/api/client";
import { useAuthStore, useCandidateStore, useToastStore } from "../src/stores/appStore";
import { RecordPage } from "../src/pages/RecordPage";
import { LedgerPage } from "../src/pages/LedgerPage";
import { RecordHomeView } from "../src/ui/RecordHomeView";
import { LedgerView } from "../src/ui/LedgerView";
import { SettlementSheet } from "../src/ui/SettlementSheet";
import { counterpartyBalanceView, receivableNote, settlementView } from "../src/utils/viewModels";
import { kstToday } from "../src/utils/useLedgerData";
import { snapshot, until } from "./review-fixture";
import type { CounterpartyDetail, UnpaidList } from "../src/types/api";

const row = { counterparty: "테스트상회", credit_total: 45000, paid_total: 5000, unpaid: 40000, oldest_days: 32, owed_count: 2 };
const unpaid: UnpaidList = { total_unpaid: 40000, counterparties: [row], has_confirmed_entries: true };
const detail: CounterpartyDetail = { ...row, entries: [], payments: [] };

test("server age passes through unchanged and a fully paid balance never shows an old age", () => {
  assert.deepEqual(counterpartyBalanceView(row), { counterparty: row.counterparty, unpaid: 40000, oldestDays: 32, owedCount: 2 });
  assert.equal(settlementView(detail).oldestDays, 32);
  assert.equal(receivableNote(unpaid), "1곳 · 최장 32일");
  assert.equal(counterpartyBalanceView({ ...row, unpaid: 0 }).oldestDays, undefined);
  assert.equal(settlementView({ ...detail, unpaid: 0 }).oldestDays, undefined);
  assert.equal(settlementView({ ...detail, oldest_days: null }).oldestDays, undefined);
  assert.equal(receivableNote({ total_unpaid: 0, counterparties: [] }), "0곳");
});

test("a missing age cannot turn a partial maximum into the oldest debt across all counterparties", () => {
  const unknown = { ...row, counterparty: "날짜 없는 상회", oldest_days: null };
  assert.equal(receivableNote({ total_unpaid: 80000, counterparties: [row, unknown] }), "2곳");
  const html = renderToStaticMarkup(<LedgerView year={2026} month={10} selectedDay={21} today={21}
    marks={{}} entries={[]} totalUnpaid={80000} balances={[row, unknown].map(counterpartyBalanceView)}
    onMonth={() => {}} onDay={() => {}} onOpenCounterparty={() => {}} onPayment={() => {}} onRecord={() => {}} />);
  assert.doesNotMatch(html, /최장/);
  assert.match(html, /32일 경과/, "a known row age remains useful even when the global maximum is unknown");
});

test("paid-off counterparties do not inflate the home count or age", () => {
  const rows = [row, { ...row, counterparty: "완납 상회", unpaid: 0, oldest_days: 90 }];
  assert.equal(receivableNote({ total_unpaid: 40000, counterparties: rows }), "1곳 · 최장 32일");
  const html = renderToStaticMarkup(<LedgerView year={2026} month={10} selectedDay={21} today={21}
    marks={{}} entries={[]} totalUnpaid={40000} balances={rows.map(counterpartyBalanceView)}
    onMonth={() => {}} onDay={() => {}} onOpenCounterparty={() => {}} onPayment={() => {}} onRecord={() => {}} />);
  assert.match(html, /1곳/);
  assert.doesNotMatch(html, /2곳|완납 상회/);
});

for (const page of ["record", "ledger"] as const) {
  test(`${page} connects server ages, cash count and copy notification without writing a transaction`, async () => {
    const previous = api.defaults.adapter;
    localStorage.clear(); useToastStore.getState().clear(); useCandidateStore.getState().setCandidate(null);
    useAuthStore.getState().setCookieSession("connection-owner");
    let writes = 0;
    let finishBusiness!: () => void;
    api.defaults.adapter = async (config) => {
      if (config.method !== "get") writes++;
      if (config.url?.endsWith("/business")) return new Promise((resolve) => {
        finishBusiness = () => resolve({ config, status: 200, statusText: "OK", headers: {},
          data: { registered: true, business_name: "테스트식자재", business_number_masked: "123-45-*****" } });
      });
      const data = config.url?.endsWith("/unpaid") ? unpaid : config.url?.endsWith("/business")
        ? { registered: true, business_name: "테스트식자재", business_number_masked: "123-45-*****" }
        : config.url?.includes("/counterparty/") ? detail
        : { entries: [
          { ...snapshot, status: "confirmed", credit: false, kst_date: kstToday() },
          { ...snapshot, entry_id: "candidate", status: "candidate", credit: false, kst_date: kstToday() },
          { ...snapshot, entry_id: "credit", status: "confirmed", credit: true, kst_date: kstToday() },
        ], daily_credit: [] };
      return { config, status: 200, statusText: "OK", headers: {}, data };
    };
    const router = createMemoryRouter([{ path: "/", element: page === "record" ? <RecordPage /> : <LedgerPage /> }]);
    let root!: ReactTestRenderer;
    try {
      await act(async () => { root = create(<RouterProvider router={router} />); });
      if (page === "record") {
        const home = root.root.findByType(RecordHomeView);
        assert.equal(home.props.unpaidNote, "1곳 · 최장 32일");
        assert.equal(home.props.cashCount, 1, "cash count excludes candidates and credit trades");
      } else {
        assert.equal(root.root.findByType(LedgerView).props.balances[0].oldestDays, 32);
        assert.equal(root.root.findByType(LedgerView).props.balances[0].owedCount, 2);
        await act(async () => root.root.findByType(LedgerView).props.onOpenCounterparty(row.counterparty));
        await act(async () => root.root.findAllByType("button").find((button) => button.children.some((child) => typeof child === "string" && child.trim() === "외상 내역 보내기"))!.props.onClick());
        assert.equal(root.root.findAllByProps({ "aria-label": "보낼 글 미리 보기" }).length, 1);
        await act(async () => finishBusiness());
        await act(async () => until(() => root.root.findAllByType(SettlementSheet).some((sheet) => sheet.props.shopName === "테스트식자재")));
        assert.equal(root.root.findAllByProps({ "aria-label": "보낼 글 미리 보기" }).length, 1, "a late optional shop name must not close the copy preview");
        const sheet = root.root.findByType(SettlementSheet);
        assert.equal(sheet.props.detail.oldestDays, 32);
        act(() => sheet.props.onCopied());
        assert.equal(useToastStore.getState().message, "복사했어요 · 카톡 대화방에 붙여 넣으세요");
      }
      assert.equal(writes, 0);
    } finally { act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous; useToastStore.getState().clear(); localStorage.clear(); }
  });
}

test("the home shows 처음 사용 only when the server says nothing was ever confirmed (PM 10/21)", async () => {
  const cases: [Partial<UnpaidList>, boolean][] = [
    [{ has_confirmed_entries: false }, true],
    [{ has_confirmed_entries: true }, false], // e.g. cash-only last month: 외상 없음, not a new owner
    [{}, false], // an older server without the field must not show 처음 사용 to everyone
  ];
  for (const [extra, expected] of cases) {
    const previous = api.defaults.adapter;
    localStorage.clear(); useCandidateStore.getState().setCandidate(null);
    useAuthStore.getState().setCookieSession("first-use-owner");
    api.defaults.adapter = async (config) => ({ config, status: 200, statusText: "OK", headers: {},
      data: config.url?.endsWith("/unpaid") ? { total_unpaid: 0, counterparties: [], ...extra }
        : config.url?.endsWith("/business") ? { registered: false } : { entries: [], daily_credit: [] } });
    const router = createMemoryRouter([{ path: "/", element: <RecordPage /> }]);
    let root!: ReactTestRenderer;
    try {
      await act(async () => { root = create(<RouterProvider router={router} />); });
      assert.equal(root.root.findByType(RecordHomeView).props.firstUse, expected, JSON.stringify(extra));
    } finally { act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous; localStorage.clear(); }
  }
});
