import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { api } from "../src/api/client";
import { RecordPage } from "../src/pages/RecordPage";
import { RecordHomeView } from "../src/ui/RecordHomeView";
import { RequestError } from "../src/components/RequestError";
import { ErrorNotice } from "../src/ui/ErrorNotice";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { snapshot, until } from "./review-fixture";

for (const scenario of ["session-success", "session-failure", "new-candidate", "unmounted", "current-success", "current-failure"] as const) {
  test(`a delayed text parse cannot replace current work: ${scenario}`, async () => {
    const previous = api.defaults.adapter;
    localStorage.clear(); sessionStorage.clear();
    useAuthStore.getState().setCookieSession("record-owner");
    useCandidateStore.getState().setCandidate(null);
    let root!: ReactTestRenderer;
    let release = () => {};
    let started = false;
    api.defaults.adapter = (config) => {
      const response = (data: unknown) => ({ config, data, status: 200, statusText: "OK", headers: {} });
      if (config.url?.endsWith("/parse")) {
        started = true;
        return new Promise((resolve, reject) => { release = () => scenario.endsWith("failure")
          ? reject(new Error("old request offline")) : resolve(response(snapshot)); });
      }
      return Promise.resolve(response(config.url?.endsWith("/unpaid") ? { total_unpaid: 0, counterparties: [] } : { entries: [], daily_credit: [] }));
    };
    const router = createMemoryRouter([{ path: "/record", element: <RecordPage /> },
      { path: "/record/confirm", element: <p>confirm destination</p> }, { path: "/other", element: <p>other destination</p> }], { initialEntries: ["/record"] });
    try {
      await act(async () => { root = create(<RouterProvider router={router} />); });
      await act(async () => {
        root.root.findByType(RecordHomeView).props.onSubmitText("새 거래 입력");
        await until(() => started);
      });
      await act(async () => {
        if (scenario === "new-candidate") useCandidateStore.getState().setCandidate({ ...snapshot, entry_id: "new-work" });
        else if (scenario === "unmounted") await router.navigate("/other");
        else if (scenario.startsWith("session")) useAuthStore.getState().setCookieSession("record-owner");
      });
      await act(async () => { release(); await new Promise((resolve) => setTimeout(resolve, 0)); });
      assert.equal(router.state.location.pathname, scenario === "unmounted" ? "/other" : scenario === "current-success" ? "/record/confirm" : "/record");
      assert.equal(useCandidateStore.getState().candidate?.entry_id ?? null, scenario === "new-candidate" ? "new-work" : scenario === "current-success" ? snapshot.entry_id : null);
      assert.equal(root.root.findAllByType(RequestError).length, 0);
      assert.equal(root.root.findAllByType(ErrorNotice).length, scenario === "current-failure" ? 1 : 0);
      if (scenario === "current-failure") assert.equal(root.root.findByType(ErrorNotice).props.kind, "network");
      if (!["unmounted", "current-success"].includes(scenario)) assert.equal(root.root.findByType(RecordHomeView).props.busy, false);
    } finally {
      release(); act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous;
      useCandidateStore.getState().setCandidate(null); localStorage.clear(); sessionStorage.clear();
    }
  });
}
