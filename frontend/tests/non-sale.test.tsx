import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AxiosError } from "axios";
import { api } from "../src/api/client";
import { describeApiFailure } from "../src/utils/apiError";
import { RecordPage } from "../src/pages/RecordPage";
import { LedgerPage } from "../src/pages/LedgerPage";
import { RecordHomeView } from "../src/ui/RecordHomeView";
import { NonSaleNotice } from "../src/components/NonSaleNotice";
import { VoiceRecorder } from "../src/components/VoiceRecorder";
import { RepairDialog } from "../src/components/RepairDialog";
import { ErrorDialog } from "../src/components/ErrorDialog";
import { PaymentSheet } from "../src/components/PaymentSheet";
import { MoveCreditDialog } from "../src/components/MoveCreditDialog";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { snapshot, until } from "./review-fixture";

const destinations = ["payment", "edit", "inquiry", "moved_credit"] as const;

for (const redirect of destinations) for (const input of ["text", "voice"] as const) {
  test(`${input} non-sale ${redirect} guides to the feature without creating or repairing a sale`, async () => {
    const previous = api.defaults.adapter;
    localStorage.clear(); sessionStorage.clear(); localStorage.setItem("echobill_guidance_sound", "off");
    useAuthStore.getState().setCookieSession("non-sale-owner");
    // New text input requires an explicit discard when a candidate exists (covered by pending-candidate-guard).
    useCandidateStore.getState().setCandidate(input === "voice" ? snapshot : null);
    const writes: string[] = [];
    const message = "서버에서 보낸 안내 문구예요.";
    // Even an unexpected partial candidate must never turn this into a repair.
    const body = { error_code: "NOT_A_SALE", message, redirect, utterance_text: "판매가 아닌 말",
      problem_fields: ["credit"], partial_candidate: snapshot };
    api.defaults.adapter = async (config) => {
      if (config.method !== "get") writes.push(config.url!);
      if (config.url?.endsWith("/parse")) throw new AxiosError("non-sale", undefined, config, undefined,
        { config, status: 400, statusText: "Bad Request", headers: {}, data: body });
      return { config, status: 200, statusText: "OK", headers: {}, data: config.url?.endsWith("/unpaid")
        ? { total_unpaid: 10000, counterparties: [{ counterparty: "가나상회", unpaid: 10000 }] }
        : { entries: [], daily_credit: [] } };
    };
    const router = createMemoryRouter([{ path: "/record", element: <RecordPage /> },
      { path: "/ledger", element: <LedgerPage /> }], { initialEntries: ["/record"] });
    let root!: ReactTestRenderer;
    try {
      await act(async () => { root = create(<RouterProvider router={router} />); });
      await act(async () => {
        // Start with a dirty text draft to verify that the explicit action is not blocked.
        root.root.findByType(RecordHomeView).props.onTextChange("판매가 아닌 말");
      });
      await act(async () => {
        if (input === "text") await root.root.findByType(RecordHomeView).props.onSubmitText("판매가 아닌 말");
        else root.root.findByType(VoiceRecorder).props.onFailure(describeApiFailure(new AxiosError("non-sale", undefined, undefined, undefined,
          { status: 400, statusText: "Bad Request", headers: {}, config: {} as never, data: body })));
      });
      await act(async () => { await until(() => root.root.findAllByType(NonSaleNotice).length === 1); });
      const notice = root.root.findByType(NonSaleNotice);
      assert.equal(notice.props.message, message);
      assert.equal(root.root.findAllByType(RepairDialog).length, 0);
      assert.equal(root.root.findAllByType(ErrorDialog).length, 0);
      assert.doesNotMatch(JSON.stringify(root.toJSON()), /글자로 고쳐서 다시 보내/);
      await act(async () => { notice.props.onContinue(); });
      assert.equal([...router.state.blockers.values()].some((blocker) => blocker.state === "blocked"), false);
      assert.equal(router.state.location.pathname, redirect === "payment" || redirect === "edit" ? "/ledger" : "/record");
      assert.equal(root.root.findAllByType(PaymentSheet).length, redirect === "payment" ? 1 : 0);
      assert.equal(root.root.findAllByType(MoveCreditDialog).length, redirect === "moved_credit" ? 1 : 0);
      assert.equal(root.root.findAllByType(NonSaleNotice).length, 0);
      assert.equal(useCandidateStore.getState().candidate?.entry_id, input === "voice" ? snapshot.entry_id : undefined);
      assert.deepEqual(writes, input === "text" ? ["/api/v1/ledger/parse"] : []);
    } finally {
      act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous;
      useCandidateStore.getState().setCandidate(null); localStorage.clear(); sessionStorage.clear();
    }
  });
}

test("a missing non-sale redirect still displays guidance and offers the ledger", () => {
  const failure = describeApiFailure(new AxiosError("non-sale", undefined, undefined, undefined,
    { status: 400, statusText: "Bad Request", headers: {}, config: {} as never,
      data: { detail: { error_code: "NOT_A_SALE", partial_candidate: snapshot, problem_fields: ["credit"] } } }));
  assert.equal(failure.redirect, undefined);
  assert.equal(failure.partialCandidate, undefined);
  assert.deepEqual(failure.problemFields, []);
  assert.match(failure.message, /장부에서 확인/);
});

for (const action of ["close", "session", "same-owner"] as const) {
  test(`non-sale ${action} dismisses the notice without changing an existing candidate`, async () => {
    const previous = api.defaults.adapter;
    localStorage.clear(); sessionStorage.clear(); localStorage.setItem("echobill_guidance_sound", "off");
    useAuthStore.getState().setCookieSession("dismiss-owner");
    useCandidateStore.getState().setCandidate(snapshot);
    api.defaults.adapter = async (config) => ({ config, status: 200, statusText: "OK", headers: {},
      data: config.url?.endsWith("/unpaid") ? { total_unpaid: 0, counterparties: [] } : { entries: [], daily_credit: [] } });
    const router = createMemoryRouter([{ path: "/record", element: <RecordPage /> }], { initialEntries: ["/record"] });
    let root!: ReactTestRenderer;
    try {
      await act(async () => { root = create(<RouterProvider router={router} />); });
      await act(async () => {
        root.root.findByType(RecordHomeView).props.onTextChange("남겨 둔 입력");
        root.root.findByType(VoiceRecorder).props.onFailure({ kind: "input", errorCode: "NOT_A_SALE", redirect: "edit", message: "장부에서 고쳐 주세요." });
      });
      await act(async () => {
        if (action === "close") root.root.findByType(NonSaleNotice).props.onClose();
        else useAuthStore.getState().setCookieSession(action === "same-owner" ? "dismiss-owner" : null);
      });
      assert.equal(root.root.findAllByType(NonSaleNotice).length, 0);
      if (action === "close") {
        assert.equal(root.root.findByType(RecordHomeView).props.text, "남겨 둔 입력");
        assert.equal(useCandidateStore.getState().candidate?.entry_id, snapshot.entry_id);
      }
    } finally {
      act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous;
      useCandidateStore.getState().setCandidate(null); localStorage.clear(); sessionStorage.clear();
    }
  });
}

for (const stale of [false, true]) {
  test(`payment navigation is consumed once and rejects a stale session: ${stale}`, async () => {
    const previous = api.defaults.adapter;
    useAuthStore.getState().setCookieSession("payment-owner");
    const { currentSessionEpoch } = await import("../src/api/client");
    const request = { owner: "payment-owner", epoch: currentSessionEpoch() - (stale ? 1 : 0) };
    api.defaults.adapter = async (config) => ({ config, status: 200, statusText: "OK", headers: {},
      data: config.url?.endsWith("/unpaid") ? { total_unpaid: 10000, counterparties: [{ counterparty: "가나상회", unpaid: 10000 }] }
        : { entries: [], daily_credit: [] } });
    const router = createMemoryRouter([{ path: "/ledger", element: <LedgerPage /> }],
      { initialEntries: [{ pathname: "/ledger", state: { openPayment: request } }] });
    let root!: ReactTestRenderer;
    try {
      await act(async () => { root = create(<RouterProvider router={router} />); });
      assert.equal(root.root.findAllByType(PaymentSheet).length, stale ? 0 : 1);
      assert.equal(router.state.location.state?.openPayment, undefined);
      if (!stale) {
        assert.equal(root.root.findByType(PaymentSheet).props.counterparty, "");
        act(() => root.root.findByType(PaymentSheet).props.onAmount("5000"));
        const submit = root.root.findAllByType("button").find(button => button.children.includes("입금 등록"));
        assert.equal(submit?.props.disabled, true, "a voice redirect requires an explicit counterparty choice");
        await act(async () => { root.root.findByType(PaymentSheet).props.onClose(); });
        await act(async () => { await router.navigate("/ledger", { replace: true, state: router.state.location.state }); });
        assert.equal(root.root.findAllByType(PaymentSheet).length, 0);
      }
    } finally { act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous; }
  });
}
