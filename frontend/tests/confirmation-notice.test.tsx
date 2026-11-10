import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { renderToStaticMarkup } from "react-dom/server";
import { api } from "../src/api/client";
import { useAuthStore, useCandidateStore, useToastStore } from "../src/stores/appStore";
import { ConfirmPage } from "../src/pages/ConfirmPage";
import { LedgerPage } from "../src/pages/LedgerPage";
import { ErrorNotice } from "../src/ui/ErrorNotice";
import { ConfirmView } from "../src/ui/ConfirmView";
import { startStartupLedger } from "../src/utils/startupLedger";
import { kstToday } from "../src/utils/useLedgerData";
import { snapshot, noop, until } from "./review-fixture";

for (const scenario of ["credit", "paid", "balance-failed"] as const) {
  test(`confirmation ${scenario}: show only a fresh server balance without repeating feedback`, async () => {
    const previous = api.defaults.adapter;
    let root!: ReactTestRenderer;
    let writes = 0;
    let balanceReads = 0;
    let release = () => {};
    localStorage.clear(); sessionStorage.clear();
    useAuthStore.getState().setCookieSession("confirmation-owner");
    useCandidateStore.getState().setCandidate({ ...snapshot, credit: scenario !== "paid" });
    useToastStore.getState().clear();
    api.defaults.adapter = (config) => {
      const response = (data: unknown) => ({ config, status: 200, statusText: "OK", headers: {}, data });
      if (config.url?.endsWith("/feedback")) {
        writes++;
        return Promise.resolve(response({ ...snapshot, status: "confirmed", credit: scenario !== "paid", counterparty: "확정상사" }));
      }
      if (config.url?.endsWith("/ledger")) return Promise.resolve(response({ entries: [], daily_credit: [] }));
      assert.ok(config.url?.endsWith("/unpaid"));
      balanceReads++;
      if (!writes) return Promise.resolve(response({ total_unpaid: 1, counterparties: [{ counterparty: "확정상사", unpaid: 1 }] }));
      if (scenario === "balance-failed") return Promise.reject(new Error("offline"));
      return new Promise((resolve) => { release = () => resolve(response({ total_unpaid: 73500,
        counterparties: [{ counterparty: "확정상사", unpaid: 73500, credit_total: 80000, paid_total: 6500 }] })); });
    };
    const startup = startStartupLedger(kstToday().slice(0, 7));
    startup.adopt("confirmation-owner");
    const router = createMemoryRouter([{ path: "/record/confirm", element: <ConfirmPage /> },
      { path: "/ledger", element: <LedgerPage /> }, { path: "/record", element: <p>record</p> }], { initialEntries: ["/record/confirm"] });
    try {
      await until(() => balanceReads === 1);
      await act(async () => { root = create(<RouterProvider router={router} />); });
      await act(async () => {
        root.root.findByType(ConfirmView).props.onConfirm();
        await until(() => router.state.location.pathname === "/ledger" && balanceReads === 2);
      });
      assert.equal(useToastStore.getState().message, "장부에 확정했습니다.", "do not guess the accumulated balance from the candidate");
      if (scenario !== "balance-failed") await act(async () => release());
      assert.equal(useToastStore.getState().message, scenario === "credit" ? "확정상사 받을 돈 총 73,500원" : "장부에 확정했습니다.");
      assert.equal(writes, 1);
      if (scenario === "credit") {
        assert.equal(router.state.location.state?.confirmation, undefined);
        useToastStore.getState().clear();
        await act(async () => router.navigate("/record"));
        await act(async () => router.navigate(-1));
        assert.equal(useToastStore.getState().message, null, "returning to the ledger must not repeat the confirmation toast");
      }
    } finally {
      release(); act(() => root?.unmount()); router.dispose(); startup.discard();
      api.defaults.adapter = previous; useCandidateStore.getState().setCandidate(null); useToastStore.getState().clear(); localStorage.clear();
    }
  });
}

test("already-confirmed guidance states its title once and directs the user to the ledger", () => {
  const html = renderToStaticMarkup(<ErrorNotice kind="alreadyConfirmed" onRetry={noop} onClose={noop} />);
  // The title is also the dialog's accessible name; the visible body has its own instruction.
  assert.match(html, />이미 장부에 저장된 내용이에요<\/h1>/);
  assert.match(html, />장부에서 확인해 주세요<\/p>/);
  assert.doesNotMatch(html, /이미 장부에 저장된 내용이에요\. 장부에서/);
});
