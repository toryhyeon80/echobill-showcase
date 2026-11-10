import { useState } from "react";
import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { api } from "../src/api/client";
import { ClarificationCard } from "../src/components/ClarificationCard";
import { VoiceRecorder } from "../src/components/VoiceRecorder";
import { ClarifyCard } from "../src/ui/ClarifyCard";
import { createCandidateDraft, type CandidateDraft } from "../src/utils/candidateDraft";
import { useAuthStore } from "../src/stores/appStore";
import { snapshot, noop, until } from "./review-fixture";
import { AxiosError } from "axios";

for (const scenario of ["current", "same-owner-session", "new-owner", "unmounted"] as const) {
  for (const outcome of ["success", "failure"] as const) {
  test(`a delayed clarification voice answer belongs only to its original session: ${scenario}/${outcome}`, async () => {
    const previous = api.defaults.adapter;
    localStorage.clear(); sessionStorage.clear();
    useAuthStore.getState().setCookieSession("voice-owner", ["qty"]);
    const draft = createCandidateDraft(snapshot);
    draft.items[0].qty = "";
    const changes: CandidateDraft[] = [];
    let failure: unknown;
    let root!: ReactTestRenderer, pending!: Promise<void>, release = noop, started = false;
    api.defaults.adapter = (config) => new Promise((resolve, reject) => {
      started = true;
      release = () => outcome === "failure" ? reject(new Error("synthetic old connection failure")) : resolve({ config, status: 200, statusText: "OK", headers: {},
        data: { status: "ok", field: "qty", value: 3, unit: "박스", utterance_text: "세 박스" } });
    });
    try {
      await act(async () => { root = create(<ClarificationCard draft={draft} busy={false}
        onChange={(next) => changes.push(next)} onFinish={noop} onClose={noop} />); });
      // Inject the answer directly; the recorder is mounted before the start gesture.
      await act(async () => {
        pending = root.root.findByType(VoiceRecorder).props.onAnswer(new Blob(["synthetic audio"], { type: "audio/webm" }))
          .catch((error: unknown) => { failure = error; });
        await until(() => started);
      });
      await act(async () => {
        if (scenario === "same-owner-session") useAuthStore.getState().setCookieSession("voice-owner", ["qty"]);
        if (scenario === "new-owner") useAuthStore.getState().setCookieSession("next-owner", ["qty"]);
        if (scenario === "unmounted") root.unmount();
        release(); await pending;
      });
      assert.equal(changes.length, scenario === "current" && outcome === "success" ? 1 : 0);
      if (changes.length) assert.equal(changes[0].items[0].qty, "3");
      assert.equal(failure instanceof Error, scenario === "current" && outcome === "failure", "stale errors must not reach the current screen");
    } finally {
      release();
      if (scenario !== "unmounted") act(() => root?.unmount());
      api.defaults.adapter = previous;
      localStorage.clear(); sessionStorage.clear();
    }
  });
  }
}

/** Exercise the connected question, rather than a class name, after a real user/voice answer. */
function MismatchSelectionHarness({ onFinish }: { onFinish: () => void }) {
  const [draft, setDraft] = useState(() => createCandidateDraft({ ...snapshot,
    items: [{ name: "무", qty: 2, unit: "개", unit_price: 5000, amount: 10000 }], total_amount: 20000 }));
  return <ClarificationCard draft={draft} busy={false} onChange={setDraft}
    onFinish={onFinish} onClose={noop} initialField="total_amount" amountChoices={[20000, 10000]} voiceFields={["total"]} />;
}

const renderedText = (node: { children: unknown[] }): string => node.children.map((child) =>
  typeof child === "string" ? child : renderedText(child as { children: unknown[] })).join("");
const mismatchButton = (root: ReactTestRenderer, text: string) => root.root.findAllByType("button")
  .find((node) => renderedText(node) === text)!;

for (const outcome of ["unrecognized", "http-error"] as const) {
  test(`failed voice answers retain the heard text without filling the draft: ${outcome}`, async () => {
    const previous = api.defaults.adapter;
    useAuthStore.getState().setCookieSession("heard-owner", ["total"]);
    let root!: ReactTestRenderer, changes = 0;
    const draft = createCandidateDraft({ ...snapshot, total_amount: null });
    api.defaults.adapter = async (config) => {
      const response = { config, status: 200, statusText: "OK", headers: {},
        data: { status: "failed", field: "total", value: null, utterance_text: "육만 천 원" } };
      if (outcome === "http-error") throw new AxiosError("unrecognized", "ERR_BAD_REQUEST", config, undefined,
        { ...response, status: 400, data: { error_code: "VOICE_ANSWER_FAILED", message: "답을 확인해 주세요.", utterance_text: "육만 천 원" } });
      return response;
    };
    try {
      await act(async () => { root = create(<ClarificationCard draft={draft} busy={false}
        onChange={() => changes++} onFinish={noop} onClose={noop} />); });
      // Inject the answer directly; the recorder is mounted before the start gesture.
      await act(async () => {
        const recorder = root.root.findByType(VoiceRecorder);
        try { await recorder.props.onAnswer(new Blob(["audio"])); }
        catch (error) { const { describeApiFailure } = await import("../src/utils/apiError"); recorder.props.onFailure(describeApiFailure(error)); }
      });
      assert.match(JSON.stringify(root.toJSON()), /이렇게 들렸어요: 육만 천 원/);
      assert.equal(changes, 0);
    } finally { act(() => root?.unmount()); api.defaults.adapter = previous; localStorage.clear(); sessionStorage.clear(); }
  });
}

test("mismatch choices show no implicit answer, then expose the selected amount and changes", async () => {
  let root!: ReactTestRenderer;
  try {
    await act(async () => { root = create(<MismatchSelectionHarness onFinish={noop} />); });
    assert.equal(mismatchButton(root, "20,000원").props["aria-pressed"], false);
    assert.equal(mismatchButton(root, "10,000원").props["aria-pressed"], false);
    assert.equal(mismatchButton(root, "입력 내용 확인하기").props.disabled, true);
    act(() => mismatchButton(root, "20,000원").props.onClick());
    assert.equal(mismatchButton(root, "20,000원").props["aria-pressed"], true);
    act(() => mismatchButton(root, "10,000원").props.onClick());
    assert.equal(mismatchButton(root, "10,000원").props["aria-pressed"], true);
    assert.equal(mismatchButton(root, "20,000원").props["aria-pressed"], false);
    assert.equal(mismatchButton(root, "입력 내용 확인하기").props.disabled, false);
  } finally { act(() => root?.unmount()); }
});

test("a successful mismatch voice answer visibly selects its amount without advancing automatically", async () => {
  const previous = api.defaults.adapter;
  let root!: ReactTestRenderer, finishes = 0;
  localStorage.clear(); sessionStorage.clear();
  useAuthStore.getState().setCookieSession("mismatch-voice-owner", ["total"]);
  api.defaults.adapter = async (config) => ({ config, status: 200, statusText: "OK", headers: {},
    data: { status: "ok", field: "total", value: 10000, utterance_text: "일만 원" } });
  try {
    await act(async () => { root = create(<MismatchSelectionHarness onFinish={() => finishes++} />); });
      // Inject the answer directly; the recorder is mounted before the start gesture.
    await act(async () => {
      await root.root.findByType(VoiceRecorder).props.onAnswer(new Blob(["synthetic audio"], { type: "audio/webm" }));
    });
    assert.equal(mismatchButton(root, "10,000원").props["aria-pressed"], true);
    assert.equal(mismatchButton(root, "20,000원").props["aria-pressed"], false);
    assert.equal(mismatchButton(root, "입력 내용 확인하기").props.disabled, false);
    assert.equal(finishes, 0);
    act(() => mismatchButton(root, "입력 내용 확인하기").props.onClick());
    assert.equal(finishes, 1);
  } finally {
    act(() => root?.unmount()); api.defaults.adapter = previous;
    localStorage.clear(); sessionStorage.clear();
  }
});
