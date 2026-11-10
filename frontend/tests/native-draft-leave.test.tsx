import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AxiosError } from "axios";
import { api } from "../src/api/client";
import { snapshot, until } from "./review-fixture";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { createCandidateDraft } from "../src/utils/candidateDraft";
import { readRepairDraft, saveRepairDraft } from "../src/utils/repairStorage";
import { RecordPage } from "../src/pages/RecordPage";
import { RepairDialog } from "../src/components/RepairDialog";
import { LeaveDraftDialog } from "../src/ui/LeaveDraftDialog";

for (const outcome of ["idle", "success", "failure", "newer"] as const) {
  test(`native leave preserves the draft and ignores abandoned repair responses: ${outcome}`, async () => {
    localStorage.clear(); sessionStorage.clear();
    useAuthStore.getState().setCookieSession("native-owner");
    useCandidateStore.getState().setCandidate(null);
    const previous = api.defaults.adapter;
    const previousWindow = window;
    // Each simulated webview owns its event listeners and native history.
    Object.assign(globalThis, { window: Object.assign(new EventTarget(), { setTimeout, clearTimeout, setInterval, clearInterval }) });
    const original = { idx: 0, key: "initial" };
    let externalMoves = 0;
    Object.assign(window, { history: { state: original,
      pushState(state: unknown) { this.state = state; },
      back() { externalMoves++; }, go() { externalMoves++; } } });
    saveRepairDraft({ owner: "native-owner", draft: { ...createCandidateDraft(snapshot), candidateId: null, credit: true },
      utterance: snapshot.utterance_text, transcript: null, progress: { field: "credit" },
      failure: { kind: "input", errorCode: "MISSING_REQUIRED_FIELD", message: "외상인가요?", problemFields: ["credit"] } });
    const finishes: Array<() => void> = [];
    api.defaults.adapter = async config => {
      if (config.url?.endsWith("/parse")) return new Promise((resolve, reject) => {
        finishes.push(() => outcome === "failure"
          ? reject(new AxiosError("late failure", "ERR_BAD_RESPONSE", config, undefined, { config, status: 500, statusText: "Error", headers: {}, data: {} }))
          : resolve({ config, status: 200, statusText: "OK", headers: {}, data: { ...snapshot, parser_type: "manual" } }));
      });
      return ({ config, status: 200, statusText: "OK", headers: {},
      data: config.url?.endsWith("/unpaid") ? { total_unpaid: 0, counterparties: [] } : { entries: [], daily_credit: [] } });
    };
    const router = createMemoryRouter([{ path: "/record", element: <RecordPage /> }, { path: "/record/confirm", element: <p>confirmation</p> }], { initialEntries: ["/record"] });
    let root!: ReactTestRenderer;
    try {
      await act(async () => { root = create(<RouterProvider router={router} />); });
      act(() => root.root.findAllByType("button").find(b => b.children.includes("이어서 입력하기"))!.props.onClick());
      assert.equal(root.root.findAllByType(RepairDialog).length, 1);
      if (outcome !== "idle") {
        act(() => { root.root.findByType(RepairDialog).props.onSubmit(); });
        await act(async () => { await until(() => finishes.length === 1); });
      }
      const back = new Event("popstate");
      Object.assign(back, { state: original });
      act(() => window.dispatchEvent(back));
      assert.equal(root.root.findByType(LeaveDraftDialog).props.open, true);
      act(() => root.root.findByType(LeaveDraftDialog).props.onLeave());
      if (outcome === "success" || outcome === "failure") {
        await act(async () => { finishes[0](); await new Promise(resolve => setTimeout(resolve, 0)); });
      }
      assert.equal(router.state.location.pathname, "/record");
      assert.equal(useCandidateStore.getState().candidate, null);
      assert.equal(root.root.findAllByType(RepairDialog).length, 0);
      assert.equal(externalMoves, 0);
      assert.equal(readRepairDraft("native-owner")?.utterance, snapshot.utterance_text);
      act(() => root.root.findAllByType("button").find(b => b.children.includes("이어서 입력하기"))!.props.onClick());
      assert.equal(root.root.findAllByType(RepairDialog).length, 1);
      assert.equal(root.root.findByType(RepairDialog).props.busy, false);
      if (outcome === "newer") {
        act(() => { root.root.findByType(RepairDialog).props.onSubmit(); });
        await act(async () => { await until(() => finishes.length === 2); });
        await act(async () => { finishes[0](); await new Promise(resolve => setTimeout(resolve, 0)); });
        assert.equal(router.state.location.pathname, "/record");
        assert.equal(root.root.findByType(RepairDialog).props.busy, true, "old completion must not unlock the new submission");
        await act(async () => { finishes[1](); await until(() => router.state.location.pathname === "/record/confirm"); });
      }
    } finally {
      act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous;
      Object.assign(globalThis, { window: previousWindow }); localStorage.clear(); sessionStorage.clear();
    }
  });

}
