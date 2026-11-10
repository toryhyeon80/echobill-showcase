import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { api } from "../src/api/client";
import { RecordPage } from "../src/pages/RecordPage";
import { RecordHomeView } from "../src/ui/RecordHomeView";
import { ConfirmDialog } from "../src/ui/ConfirmDialog";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { snapshot, until } from "./review-fixture";

for (const action of ["voice", "text"] as const) {
  for (const choice of ["resume", "discard", "changed-session", "changed-candidate"] as const) {
    test(`pending candidate survives new ${action} input until explicit choice: ${choice}`, async () => {
      const previous = api.defaults.adapter;
      localStorage.clear(); sessionStorage.clear();
      useAuthStore.getState().setCookieSession("guard-owner");
      useCandidateStore.getState().setCandidate(snapshot);
      let parses = 0, microphones = 0, root!: ReactTestRenderer;
      const oldNavigator = Object.getOwnPropertyDescriptor(globalThis, "navigator");
      Object.defineProperty(globalThis, "navigator", { configurable: true, value: {
        mediaDevices: { getUserMedia: () => { microphones++; return new Promise(() => {}); } },
      } });
      Object.assign(globalThis, { MediaRecorder: class { static isTypeSupported() { return true; } } });
      api.defaults.adapter = async (config) => {
        if (config.url?.endsWith("/parse")) parses++;
        return { config, status: 200, statusText: "OK", headers: {}, data: config.url?.endsWith("/parse")
          ? { ...snapshot, entry_id: "new" } : config.url?.endsWith("/unpaid") ? { total_unpaid: 0, counterparties: [] } : { entries: [], daily_credit: [] } };
      };
      const router = createMemoryRouter([{ path: "/record", element: <RecordPage /> },
        { path: "/record/confirm", element: <p>confirm</p> }], { initialEntries: ["/record"] });
      try {
        await act(async () => { root = create(<RouterProvider router={router} />); });
        if (action === "text") act(() => root.root.findByType(RecordHomeView).props.onTextChange("새로운 거래"));
        act(() => {
          const home = root.root.findByType(RecordHomeView);
          if (action === "voice") home.props.onRecord(); else home.props.onSubmitText("새로운 거래");
        });
        assert.equal(parses, 0); assert.equal(microphones, 0);
        assert.equal(useCandidateStore.getState().candidate?.entry_id, snapshot.entry_id);
        const dialog = root.root.findAllByType(ConfirmDialog).find((node) => node.props.open);
        assert.ok(dialog, "new input must offer a choice before replacing the saved candidate");
        const confirm = dialog.props.onConfirm;
        if (choice === "changed-session") act(() => useAuthStore.getState().setCookieSession("guard-owner"));
        if (choice === "changed-candidate") act(() => useCandidateStore.getState().setCandidate({ ...snapshot, entry_id: "other" }));
        await act(async () => { if (choice === "resume") dialog.props.onCancel(); else confirm(); });
        if (choice === "discard") {
          await until(() => action === "voice" ? microphones === 1 : parses === 1);
          assert.equal(useCandidateStore.getState().candidate?.entry_id ?? null, action === "voice" ? null : "new");
        } else {
          assert.equal(parses, 0); assert.equal(microphones, 0);
          assert.equal(useCandidateStore.getState().candidate?.entry_id, choice === "changed-candidate" ? "other" : snapshot.entry_id);
          assert.equal(router.state.location.pathname, choice === "resume" ? "/record/confirm" : "/record");
        }
      } finally {
        act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous;
        useCandidateStore.getState().setCandidate(null);
        if (oldNavigator) Object.defineProperty(globalThis, "navigator", oldNavigator);
        localStorage.clear(); sessionStorage.clear();
      }
    });
  }
}
