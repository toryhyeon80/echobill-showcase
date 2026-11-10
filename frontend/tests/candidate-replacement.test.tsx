import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { ConfirmPage } from "../src/pages/ConfirmPage";
import { ConfirmView } from "../src/ui/ConfirmView";
import { BottomSheet } from "../src/ui/BottomSheet";
import { createCandidateDraft, saveCandidateDraft } from "../src/utils/candidateDraft";
import { snapshot, until } from "./review-fixture";
import { api } from "../src/api/client";

for (const replacementKind of ["different-id", "same-id", "new-session"] as const) {
  test(`late confirmation cannot clear replacement work or unlock its request (${replacementKind})`, async () => {
    localStorage.clear(); sessionStorage.clear();
    useAuthStore.getState().setCookieSession("candidate-owner");
    useCandidateStore.getState().setCandidate(snapshot);
    const previous = api.defaults.adapter;
    const finish: Array<() => void> = [];
    api.defaults.adapter = (config) => {
      const response = (data: unknown) => ({ config, status: 200, statusText: "OK", headers: {}, data });
      return config.url?.endsWith("/feedback") ? new Promise((resolve) => finish.push(() => resolve(response({ ...snapshot, status: "confirmed" }))))
        : Promise.resolve(response({ entries: [], counterparties: [] }));
    };
    const router = createMemoryRouter([{ path: "/confirm", element: <ConfirmPage /> }, { path: "/ledger", element: <p>장부</p> }], { initialEntries: ["/confirm"] });
    let root!: ReactTestRenderer;
    const confirm = () => root.root.findByType(ConfirmView);
    try {
      await act(async () => { root = create(<RouterProvider router={router} />); });
      await act(async () => { confirm().props.onConfirm(); await until(() => finish.length === 1); });
      const replacement = replacementKind === "new-session" ? snapshot
        : { ...snapshot, entry_id: replacementKind === "same-id" ? snapshot.entry_id : "replacement", counterparty: "새후보상사" };
      await act(async () => {
        if (replacementKind === "new-session") useAuthStore.getState().setCookieSession("candidate-owner");
        else useCandidateStore.getState().setCandidate(replacement);
      });
      assert.equal(confirm().props.confirming, false, "replacement work must not wait for the old request");
      await act(async () => { confirm().props.onConfirm(); await until(() => finish.length === 2); });
      await act(async () => finish[0]());
      assert.equal(router.state.location.pathname, "/confirm");
      assert.equal(useCandidateStore.getState().candidate, replacement);
      assert.equal(confirm().props.confirming, true, "an old finally must not unlock a newer submission");
      await act(async () => finish[1]());
      assert.equal(router.state.location.pathname, "/ledger");
    } finally {
      act(() => root?.unmount()); router.dispose(); finish.forEach((resolve) => resolve());
      api.defaults.adapter = previous; useCandidateStore.getState().setCandidate(null); localStorage.clear(); sessionStorage.clear();
    }
  });
}

test("switching candidates discards an unrelated restore prompt instead of applying its old edits", async () => {
  localStorage.clear(); sessionStorage.clear();
  useAuthStore.getState().setCookieSession("candidate-owner");
  useCandidateStore.getState().setCandidate(snapshot);
  saveCandidateDraft({ ...createCandidateDraft(snapshot, snapshot.entry_id), counterparty: "이전수정상사" });
  let root!: ReactTestRenderer;
  const router = createMemoryRouter([{ path: "/confirm", element: <ConfirmPage /> }], { initialEntries: ["/confirm"] });
  try {
    await act(async () => { root = create(<RouterProvider router={router} />); });
    assert.match(JSON.stringify(root.toJSON()), /저장하지 않은 수정/);
    await act(async () => useCandidateStore.getState().setCandidate({ ...snapshot, entry_id: "new-candidate", counterparty: "새후보상사" }));
    assert.doesNotMatch(JSON.stringify(root.toJSON()), /저장하지 않은 수정|이전수정상사/);
    assert.equal(root.root.findByType(ConfirmView).props.candidate.counterparty, "새후보상사");
  } finally { act(() => root?.unmount()); router.dispose(); useCandidateStore.getState().setCandidate(null); localStorage.clear(); sessionStorage.clear(); }
});

test("an old editor closes when a new candidate replaces its content", async () => {
  const previous = api.defaults.adapter;
  api.defaults.adapter = async (config) => ({ config, status: 200, statusText: "OK", headers: {}, data: { entries: [], counterparties: [] } });
  localStorage.clear(); sessionStorage.clear();
  useAuthStore.getState().setCookieSession("candidate-owner");
  useCandidateStore.getState().setCandidate(snapshot);
  let root!: ReactTestRenderer;
  const router = createMemoryRouter([{ path: "/confirm", element: <ConfirmPage /> }], { initialEntries: ["/confirm"] });
  try {
    await act(async () => { root = create(<RouterProvider router={router} />); });
    act(() => root.root.findByType(ConfirmView).props.onEdit());
    assert.equal(root.root.findByType(BottomSheet).props.open, true);
    await act(async () => useCandidateStore.getState().setCandidate({ ...snapshot, entry_id: "new-candidate", counterparty: "새후보상사" }));
    assert.equal(root.root.findByType(BottomSheet).props.open, false);
  } finally { act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous; useCandidateStore.getState().setCandidate(null); localStorage.clear(); sessionStorage.clear(); }
});
