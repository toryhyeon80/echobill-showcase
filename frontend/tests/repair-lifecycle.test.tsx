import { useState } from "react";
import { useDraftLeaveGuard } from "../src/utils/useDraftLeaveGuard";
import { test } from "node:test";
import assert from "node:assert/strict";
import { create, act, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AxiosError } from "axios";
import { api } from "../src/api/client";
import { ConfirmPage } from "../src/pages/ConfirmPage";
import { ConfirmView } from "../src/ui/ConfirmView";
import { RecordPage } from "../src/pages/RecordPage";
import { RecordHomeView } from "../src/ui/RecordHomeView";
import { RequestError } from "../src/components/RequestError";
import { ErrorNotice } from "../src/ui/ErrorNotice";
import { describeApiFailure, errorNoticeKind } from "../src/utils/apiError";
import { RepairDialog } from "../src/components/RepairDialog";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { localStore, sessionStore } from "../src/utils/browserStorage";
import { readRepairDraft } from "../src/utils/repairStorage";

/** Browser lifecycle events and timers without a microphone or network connection. */
function storage(): Storage {
  const values = new Map<string, string>();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value); },
    removeItem: (key) => { values.delete(key); }, clear: () => values.clear(),
    get length() { return values.size; }, key: (index) => [...values.keys()][index] ?? null };
}
Object.assign(globalThis, { localStorage: storage(), sessionStorage: storage(),
  window: Object.assign(new EventTarget(), { setTimeout, clearTimeout, setInterval, clearInterval,
    location: new URL("http://localhost/record") }), document: new EventTarget() });

/** Let async request hashing and React updates settle before inspecting the screen. */
async function until(ready: () => boolean): Promise<void> {
  for (let tries = 0; tries < 100; tries++) {
    if (ready()) return;
    await new Promise((resolve) => setTimeout(resolve, 10));
  }
  assert.fail("screen did not settle");
}

for (const outcome of ["success", "reused", "rejected", "lost", "confirmed"] as const) {
  test(`repair submission ${outcome}: lifecycle cleanup preserves only unfinished work`, async () => {
    localStore.clear(); sessionStore.clear();
    useAuthStore.getState().setCookieSession("repair-owner");
    useCandidateStore.getState().setCandidate(null);
    const previous = api.defaults.adapter;
    let root: ReactTestRenderer | undefined;
    let manualRequests = 0;
    let confirmations = 0;
    const candidateReturned = outcome === "success" || outcome === "reused";
    const router = createMemoryRouter([{ path: "/record", element: <RecordPage /> },
      { path: "/record/confirm", element: <ConfirmPage /> }, { path: "/ledger", element: <p>ledger</p> }], { initialEntries: ["/record"] });
    api.defaults.adapter = async (config) => {
      const response = (data: unknown, status = 200) => ({ data, status, statusText: "OK", config, headers: {} });
      if (config.url?.endsWith("/ledger")) return response({ entries: [], daily_credit: [] });
      if (config.url?.endsWith("/unpaid")) return response({ total_unpaid: 0, counterparties: [] });
      if (config.url?.endsWith("/feedback")) {
        confirmations++;
        return response({ ...useCandidateStore.getState().candidate, status: "confirmed" });
      }
      assert.ok(config.url?.endsWith("/parse"));
      const body = JSON.parse(config.data);
      if (!body.candidate) throw new AxiosError("zero total", undefined, config, undefined, response({
        error_code: "AMOUNT_OUT_OF_RANGE", message: "금액을 확인해 주세요.", problem_fields: ["total_amount"],
        partial_candidate: { counterparty: "회귀상사", items: [{ name: "배추", qty: 2, unit: "박스", unit_price: null, amount: null }], total_amount: 0, credit: true },
      }, 400));
      manualRequests++;
      if (outcome === "confirmed") throw new AxiosError("already confirmed", undefined, config, undefined, response({
        error_code: "REPAIR_ALREADY_CONFIRMED", message: "이미 장부에 저장된 내용이에요. 장부에서 확인해 주세요.",
      }, 409));
      if (outcome === "lost") throw new AxiosError("lost response", undefined, config);
      if (outcome === "rejected") throw new AxiosError("validation rejected", undefined, config, undefined, response({
        error_code: "AMOUNT_OUT_OF_RANGE", message: "금액을 확인해 주세요.", problem_fields: ["total_amount"],
      }, 400));
      return response({ ...body.candidate, ...(outcome === "reused" ? { total_amount: 12000, counterparty: "기존상사" } : {}), entry_id: "repaired-entry", utterance_text: body.utterance_text,
        parser_type: "manual", is_fallback_used: false, created_at: "2026-10-15T00:00:00Z" });
    };
    try {
      await act(async () => { root = create(<RouterProvider router={router} />); });
      await act(async () => { root!.root.findByType(RecordHomeView).props.onSubmitText("회귀상사 배추 두 박스 0원 외상"); await until(() => root!.root.findAllByType(RepairDialog).length > 0); });
      assert.equal(root!.root.findByType(RepairDialog).props.draft.total_amount, "0");
      await act(async () => {
        const repair = root!.root.findByType(RepairDialog);
        repair.props.onChange({ ...repair.props.draft, total_amount: "15000" });
      });
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 450)); });
      assert.equal(readRepairDraft("repair-owner")?.draft.total_amount, "15000");
      await act(async () => { root!.root.findByType(RepairDialog).props.onSubmit(); await until(() => candidateReturned ? router.state.location.pathname === "/record/confirm" : outcome === "confirmed" ? root!.root.findAllByType(RequestError).some((node) => node.props.failure.errorCode === "REPAIR_ALREADY_CONFIRMED") : root!.root.findByType(RepairDialog).props.submitError !== null); });
      assert.equal(manualRequests, 1);
      if (candidateReturned) {
        assert.equal(router.state.location.pathname, "/record/confirm");
        assert.equal(useCandidateStore.getState().candidate?.entry_id, "repaired-entry");
        assert.equal(root!.root.findByType(ConfirmView).props.candidate.total, outcome === "reused" ? 12000 : 15000,
          "a reused server candidate must be shown as returned, not overwritten with the second submission");
        assert.equal(localStore.getItem("echobill_repair_draft"), null, "submission and unmount must not resurrect the draft");
        await act(async () => {
          root!.root.findByType(ConfirmView).props.onConfirm();
          await until(() => router.state.location.pathname === "/ledger");
        });
        assert.equal(confirmations, 1);
      }
      if (outcome === "confirmed") {
        assert.equal(root!.root.findAllByType(RepairDialog).length, 0);
        assert.equal(root!.root.findByType(ErrorNotice).props.kind, "alreadyConfirmed");
        assert.equal(localStore.getItem("echobill_repair_draft"), null);
        await act(async () => { root!.root.findByType(ErrorNotice).props.onRetry(); });
        assert.equal(router.state.location.pathname, "/ledger", "ledger navigation must not be blocked by the completed draft");
        assert.equal(confirmations, 0);
      }
      await act(async () => { window.dispatchEvent(new Event("pagehide")); root!.unmount(); });
      root = undefined;
      await new Promise((resolve) => setTimeout(resolve, 450));
      if (candidateReturned || outcome === "confirmed") {
        assert.equal(readRepairDraft("repair-owner"), null);
        assert.equal(localStore.getItem("echobill_repair_draft"), null);
        await act(async () => { await router.navigate("/record"); root = create(<RouterProvider router={router} />); });
        assert.equal(root!.root.findAllByType("button").some((button) => button.children.includes("이어서 입력하기")), false);
        assert.equal(manualRequests, 1);
        assert.equal(confirmations, candidateReturned ? 1 : 0);
      } else assert.equal(readRepairDraft("repair-owner")?.draft.total_amount, "15000");
    } finally {
      await act(async () => { root?.unmount(); });
      router.dispose(); api.defaults.adapter = previous;
      useCandidateStore.getState().setCandidate(null); localStore.clear(); sessionStore.clear();
    }
  });
}

test("confirmed repair has its own ledger guidance and fallback in either envelope", () => {
  for (const wrapped of [false, true]) {
    const detail = { error_code: "REPAIR_ALREADY_CONFIRMED" };
    const error = new AxiosError("confirmed", undefined, undefined, undefined,
      { status: 409, data: wrapped ? { detail } : detail, headers: {}, config: { headers: {} } } as never);
    const failure = describeApiFailure(error);
    assert.equal(failure.kind, "conflict");
    assert.equal(errorNoticeKind(failure), "alreadyConfirmed");
    assert.equal(failure.message, "이미 장부에 저장된 내용이에요. 장부에서 확인해 주세요.");
  }
});

test("finishing one draft does not disable the leave warning for the next draft", async () => {
  let controls!: { complete: () => void; edit: () => void; open: boolean };
  function Draft() {
    const [dirty, setDirty] = useState(true);
    const guard = useDraftLeaveGuard(dirty);
    controls = { complete: () => { guard.allowNavigation(); setDirty(false); }, edit: () => setDirty(true), open: guard.open };
    return <p>draft</p>;
  }
  const router = createMemoryRouter([{ path: "/record", element: <Draft /> }, { path: "/ledger", element: <p>ledger</p> }], { initialEntries: ["/record"] });
  let root!: ReactTestRenderer;
  try {
    await act(async () => { root = create(<RouterProvider router={router} />); });
    await act(async () => { controls.complete(); });
    await act(async () => { controls.edit(); });
    await act(async () => { await router.navigate("/ledger"); });
    assert.equal(router.state.location.pathname, "/record");
    assert.equal(controls.open, true);
  } finally { await act(async () => { root?.unmount(); }); router.dispose(); }
});


test("first webview back opens a draft dialog and explicit leave returns to record home", async () => {
  let guard!: ReturnType<typeof useDraftLeaveGuard>;
  const originalHistory = window.history;
  const moves: number[] = [];
  let state = { idx: 0, key: "first" };
  Object.assign(window, { history: {
    get state() { return state; },
    pushState(next: typeof state) { state = next; },
    go(delta: number) { moves.push(delta); }, back() { moves.push(-1); },
  } });
  function Draft() { guard = useDraftLeaveGuard(true); return <p>질문</p>; }
  const router = createMemoryRouter([{ path: "/record/confirm", element: <Draft /> }, { path: "/record", element: <p>기록 첫 화면</p> }], { initialEntries: ["/record/confirm"] });
  let root!: ReactTestRenderer;
  const back = () => { const event = new Event("popstate"); Object.assign(event, { state: { idx: 0, key: "first" } }); window.dispatchEvent(event); };
  try {
    await act(async () => { root = create(<RouterProvider router={router} />); });
    await act(async () => { back(); });
    assert.equal(guard.open, true); assert.deepEqual(moves, []);
    await act(async () => { guard.stay(); });
    assert.equal(guard.open, false);
    await act(async () => { back(); });
    assert.equal(guard.open, true);
    await act(async () => { guard.leave(); });
    assert.deepEqual(moves, []);
    assert.equal(router.state.location.pathname, "/record");
  } finally { await act(async () => { root?.unmount(); }); router.dispose(); Object.assign(window, { history: originalHistory }); }
});
