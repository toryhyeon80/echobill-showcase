import { useState } from "react";
import { ClarificationCard } from "../src/components/ClarificationCard";
import { ClarifyCard } from "../src/ui/ClarifyCard";
import { test } from "node:test";
import assert from "node:assert/strict";
import { AxiosError, type InternalAxiosRequestConfig } from "axios";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { api, advanceSessionEpoch } from "../src/api/client";
import { guidanceSoundEnabled, prepareGuidanceAudio, readGuidance, saveGuidanceSoundEnabled, stopGuidanceAudio } from "../src/utils/guidanceAudio";
import { useGuidanceAudio } from "../src/utils/useGuidanceAudio";
import { guidanceSummary, splitGuidanceText } from "../src/utils/guidanceText";
import { saveRepairDraft, clearRepairDraft } from "../src/utils/repairStorage";
import { createCandidateDraft } from "../src/utils/candidateDraft";
import { snapshot } from "./review-fixture";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { RecordPage } from "../src/pages/RecordPage";
import { VoiceRecorder } from "../src/components/VoiceRecorder";
import { NonSaleNotice } from "../src/components/NonSaleNotice";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { until } from "./review-fixture";
import { RecordingOverlay } from "../src/ui/RecordingOverlay";

let plays = 0, stops = 0, systemReads = 0;
let decode: () => Promise<AudioBuffer> = async () => ({} as AudioBuffer);
let failStart = false, disconnects = 0, systemCancels = 0;
let stopError: Error | null = null;
/** Silent priming is distinct from audible playback, so tests can detect late output. */
class AudioOutput {
  static latest: AudioOutput | undefined;
  state = "suspended";
  sampleRate = 48000;
  destination = {};
  constructor() { AudioOutput.latest = this; }
  async resume() { await Promise.resolve(); this.state = "running"; }
  async close() { this.state = "closed"; }
  createBuffer() { return null; }
  createBufferSource() {
    let started = false;
    return { buffer: null, onended: null, connect() {}, disconnect() { disconnects++; },
      start() {
        if (this.buffer !== null && failStart) throw new DOMException("output unavailable", "NotSupportedError");
        started = true;
        if (this.buffer !== null) plays++;
      },
      stop() {
        if (stopError) throw stopError;
        if (!started) throw new DOMException("source already stopped", "InvalidStateError");
        stops++;
      } };
  }
  decodeAudioData() { return decode(); }
}

/** Reset device preferences and network/audio doubles without real synthesis or credentials. */
function setup(ios = false) {
  stopGuidanceAudio();
  if (AudioOutput.latest) AudioOutput.latest.state = "closed";
  plays = 0; stops = 0; systemReads = 0;
  failStart = false; stopError = null; disconnects = 0; systemCancels = 0;
  decode = async () => ({} as AudioBuffer);
  saveGuidanceSoundEnabled(true);
  Object.assign(document, { visibilityState: "visible" });
  Object.assign(window, { AudioContext: AudioOutput, speechSynthesis: { cancel() { systemCancels++; }, speak() { systemReads++; } } });
  Object.assign(globalThis, { SpeechSynthesisUtterance: class { constructor(public text: string) {} } });
  Object.defineProperty(globalThis, "navigator", { configurable: true,
    value: { userAgent: ios ? "iPhone" : "Android", platform: ios ? "iPhone" : "Linux", maxTouchPoints: 1 } });
  prepareGuidanceAudio();
}

/** A valid speech response carries binary MPEG content, never a ledger candidate. */
function response(config: InternalAxiosRequestConfig) {
  return { config, status: 200, statusText: "OK", headers: { "content-type": "audio/mpeg" }, data: new Blob(["mp3"], { type: "audio/mpeg" }) };
}

test("speech uses the 80-character contract, cookie client and >=10s timeout without an idempotency key", async () => {
  const old = api.defaults.adapter;
  setup();
  let requests = 0;
  api.defaults.adapter = async (config) => {
    requests++;
    assert.equal(config.url, "/api/v1/voice/speak");
    assert.deepEqual(JSON.parse(config.data), { text: "몇 단이에요?" });
    assert.equal(config.withCredentials, true); assert.ok(config.timeout! >= 10_000);
    assert.equal(config.responseType, "blob"); assert.equal(config.headers["X-Idempotency-Key"], undefined);
    return response(config);
  };
  try {
    await readGuidance("  몇 단이에요?  ");
    assert.equal(requests, 1); assert.equal(plays, 1);

  } finally { stopGuidanceAudio(); api.defaults.adapter = old; }
});

for (const interruption of ["answer", "mute", "background", "session", "decode"] as const) {
  test(`late speech cannot play after ${interruption}`, async () => {
    const old = api.defaults.adapter;
    setup();
    let release!: () => void;
    api.defaults.adapter = (config) => interruption === "decode" ? Promise.resolve(response(config))
      : new Promise((resolve) => { release = () => resolve(response(config)); });
    if (interruption === "decode") decode = () => new Promise((resolve) => { release = () => resolve({} as AudioBuffer); });
    try {
      const playing = readGuidance("몇 단이에요?");
      for (let i = 0; i < 20 && !release; i++) await new Promise((resolve) => setTimeout(resolve, 0));
      assert.ok(release);
      if (interruption === "mute") saveGuidanceSoundEnabled(false);
      else if (interruption === "background") { Object.assign(document, { visibilityState: "hidden" }); document.dispatchEvent(new Event("visibilitychange")); }
      else if (interruption === "session") advanceSessionEpoch();
      else stopGuidanceAudio();
      release(); await playing;
      assert.equal(plays, 0); assert.equal(systemReads, 0);
    } finally { stopGuidanceAudio(); api.defaults.adapter = old; }
  });
}

test("recording preparation immediately stops audible guidance; muted guidance makes no request", async () => {
  const old = api.defaults.adapter;
  setup(); let requests = 0;
  api.defaults.adapter = async (config) => { requests++; return response(config); };
  try {
    await readGuidance("맞나요?"); assert.equal(plays, 1);
    prepareGuidanceAudio(); assert.equal(stops, 1);
    saveGuidanceSoundEnabled(false); assert.equal(guidanceSoundEnabled(), false);
    await readGuidance("다음 질문"); assert.equal(requests, 1);
  } finally { stopGuidanceAudio(); api.defaults.adapter = old; }
});

test("an already stopped browser source cannot break cancellation or the next recording", async () => {
  const old = api.defaults.adapter;
  setup();
  api.defaults.adapter = async (config) => response(config);
  try {
    await readGuidance("맞나요?");
    stopError = new DOMException("source already stopped", "InvalidStateError");
    assert.doesNotThrow(stopGuidanceAudio);
    assert.equal(disconnects, 1, "stale output is disconnected even if stop rejects");
    assert.doesNotThrow(prepareGuidanceAudio);
  } finally { stopError = null; stopGuidanceAudio(); api.defaults.adapter = old; }
});

test("unexpected output errors still disconnect output and cancel system speech before surfacing", async () => {
  const old = api.defaults.adapter;
  setup(true);
  api.defaults.adapter = async (config) => response(config);
  try {
    await readGuidance("맞나요?");
    const before = systemCancels;
    stopError = new Error("synthetic output failure");
    assert.throws(stopGuidanceAudio, /synthetic output failure/);
    assert.equal(disconnects, 1);
    assert.equal(systemCancels, before + 1);
    assert.doesNotThrow(prepareGuidanceAudio, "unexpected error must not retain an obsolete source");
  } finally { stopError = null; stopGuidanceAudio(); api.defaults.adapter = old; }
});

test("a failed stop still disconnects every scheduled speech segment", async () => {
  const old = api.defaults.adapter;
  setup();
  api.defaults.adapter = async (config) => response(config);
  try {
    await readGuidance(`${"가".repeat(70)}, ${"나".repeat(70)}, ${"다".repeat(70)}`);
    assert.ok(plays > 1);
    stopError = new Error("synthetic segment stop failure");
    const before = systemCancels;
    assert.throws(stopGuidanceAudio, /synthetic segment stop failure/);
    assert.equal(disconnects, plays, "every segment must be disconnected even if an earlier stop fails");
    assert.equal(systemCancels, before + 1);
  } finally { stopError = null; stopGuidanceAudio(); api.defaults.adapter = old; }
});

test("failed playback releases the source and permits another recording or replay", async () => {
  const old = api.defaults.adapter;
  setup(true);
  api.defaults.adapter = async (config) => response(config);
  try {
    failStart = true;
    await readGuidance("맞나요?");
    assert.equal(systemReads, 1);
    assert.equal(disconnects, 1, "failed playback must not leave an output node connected");
    assert.doesNotThrow(prepareGuidanceAudio);
    failStart = false;
    await readGuidance("다시 확인해 주세요.");
    assert.equal(plays, 1);
  } finally { failStart = false; stopGuidanceAudio(); api.defaults.adapter = old; }
});

for (const status of [400, 401, 429, 504]) {
  for (const ios of [false, true]) {
    test(`speech ${status} keeps the flow available and ${ios ? "uses iOS fallback" : "leaves Android text only"}`, async () => {
      const old = api.defaults.adapter; setup(ios);
      let expired = 0;
      const expiredListener = () => { expired++; };
      window.addEventListener("echobill:session-expired", expiredListener);
      api.defaults.adapter = async (config) => { throw new AxiosError("speech unavailable", "ERR_BAD_RESPONSE", config, undefined,
        { ...response(config), status }); };
      try {
        await readGuidance("몇 단이에요?");
        assert.equal(plays, 0); assert.equal(systemReads, ios ? 1 : 0); assert.equal(expired, 0);
      } finally { stopGuidanceAudio(); api.defaults.adapter = old; window.removeEventListener("echobill:session-expired", expiredListener); }
    });
  }
}

test("fixed failure audio never calls the synthesis API", async () => {
  setup(); const oldFetch = globalThis.fetch, old = api.defaults.adapter;
  let url: unknown;
  globalThis.fetch = async (input) => { url = input; return new Response(new Uint8Array([1, 2]), { headers: { "Content-Type": "audio/mpeg" } }); };
  api.defaults.adapter = async () => { assert.fail("fixed audio must not consume synthesis quota"); };
  try {
    await readGuidance("잘 못 들었어요.", "/audio/stt-retry.mp3");
    assert.equal(url, "/audio/stt-retry.mp3"); assert.equal(plays, 1);
  } finally { stopGuidanceAudio(); globalThis.fetch = oldFetch; api.defaults.adapter = old; }
});

for (const lifecycle of ["visible", "pageshow"] as const) {
  test(`screen unlock via ${lifecycle} discards interrupted audio and the next tap plays guidance`, async () => {
    setup(); const old = api.defaults.adapter;
    api.defaults.adapter = async (config) => response(config);
    try {
      const interrupted = AudioOutput.latest!;
      interrupted.state = "interrupted";
      if (lifecycle === "visible") document.dispatchEvent(new Event("visibilitychange"));
      else window.dispatchEvent(new Event("pageshow"));
      assert.equal(interrupted.state, "closed");
      prepareGuidanceAudio();
      assert.notEqual(AudioOutput.latest, interrupted);
      await readGuidance("다시 확인해 주세요.");
      assert.equal(plays, 1);
    } finally { stopGuidanceAudio(); api.defaults.adapter = old; }
  });
}

test("interruption while speech is downloading retries on a fresh device without replaying stale speech", async () => {
  setup(); const old = api.defaults.adapter;
  let interrupted!: AudioOutput;
  api.defaults.adapter = async (config) => { interrupted = AudioOutput.latest!; interrupted.state = "interrupted"; return response(config); };
  try {
    await readGuidance("몇 단이에요?");
    assert.equal(interrupted.state, "closed"); assert.notEqual(AudioOutput.latest, interrupted);
    assert.equal(plays, 1);
  } finally { stopGuidanceAudio(); api.defaults.adapter = old; }
});

test("automatic reading happens once per question, never per keystroke or editor reopen, and unmount stops it", async () => {
  const old = api.defaults.adapter; setup(); let requests = 0, root!: ReactTestRenderer;
  api.defaults.adapter = async (config) => { requests++; return response(config); };
  function Question({ text, id, active = true }: { text: string; id: string; active?: boolean }) { useGuidanceAudio(text, id, active); return null; }
  try {
    await act(async () => { root = create(<Question text="몇 단이에요?" id="qty" />); });
    assert.equal(requests, 1);
    await act(async () => { root.update(<Question text="몇 단이에요?" id="qty" active={false} />); });
    await act(async () => { root.update(<Question text="몇 단이에요?" id="qty" />); });
    assert.equal(requests, 1, "closing an editor must not automatically speak again");
    await act(async () => { root.update(<Question text="배추 세 단 몇 단이에요?" id="qty" />); });
    assert.equal(requests, 1);
    await act(async () => { root.update(<Question text="얼마예요?" id="total" />); });
    assert.equal(requests, 2);
    act(() => root.unmount()); assert.equal(stops, 2);
  } finally { stopGuidanceAudio(); api.defaults.adapter = old; }
});

test("guidance reads only the visible summary or the exact clarification question", () => {
  const draft = { ...createCandidateDraft(snapshot), counterparty: "바다철물", total_amount: "41000" };
  assert.equal(guidanceSummary(draft), "바다철물, 41,000원, 미수금. 이 내용이 맞나요?");
  assert.equal(guidanceSummary({ ...draft, credit: false }), "바다철물, 41,000원, 결제 완료. 이 내용이 맞나요?");
  assert.equal(guidanceSummary({ ...draft, credit: null }), "바다철물, 41,000원. 이 내용이 맞나요?");
  assert.equal(guidanceSummary(draft, "몇 단이에요?"), "몇 단이에요?");
  const long = { ...draft, counterparty: "가".repeat(200) };
  assert.equal(guidanceSummary(long), `${long.counterparty}, 41,000원, 미수금. 이 내용이 맞나요?`);
});

test("missing amounts are omitted from speech while provided amounts keep the screen format", () => {
  const draft = { ...createCandidateDraft(snapshot), counterparty: "바다철물", total_amount: "" };
  assert.equal(guidanceSummary(draft), "바다철물, 미수금. 이 내용이 맞나요?");
  assert.equal(guidanceSummary({ ...draft, credit: null }), "바다철물. 이 내용이 맞나요?");
  assert.equal(guidanceSummary({ ...draft, total_amount: "0" }), "바다철물, 0원, 미수금. 이 내용이 맞나요?");
});

test("speech prefers clause boundaries and never splits formatted amounts or decimals", () => {
  for (const amount of ["41,000원", "100,000,000원", "1.5박스"]) {
    for (let prefix = 65; prefix <= 81; prefix++) {
      const text = `${"가".repeat(prefix)}, ${amount}, 미수금. 이 내용이 맞나요?`;
      const parts = splitGuidanceText(text);
      assert.equal(parts.join(""), text);
      assert.ok(parts.every((part) => Array.from(part).length <= 80));
      assert.ok(parts.some((part) => part.includes(amount)), `${amount} must stay together`);
    }
  }
  const clause = `${"가".repeat(50)}, `;
  assert.deepEqual(splitGuidanceText(`${clause}${"나".repeat(40)}. 확인해 주세요.`),
    [clause.trimEnd(), ` ${"나".repeat(40)}. 확인해 주세요.`]);
  assert.deepEqual(splitGuidanceText("😀".repeat(81)), ["😀".repeat(80), "😀"]);
  const unbroken = `${"가".repeat(77)}41,000원`;
  assert.deepEqual(splitGuidanceText(unbroken), ["가".repeat(77), "41,000원"]);
  const spaced = `${"가".repeat(75)} 100,000,000원`;
  assert.deepEqual(splitGuidanceText(spaced), [`${"가".repeat(75)} `, "100,000,000원"]);
  assert.deepEqual(splitGuidanceText("가".repeat(80)), ["가".repeat(80)]);
  assert.throws(() => splitGuidanceText("1".repeat(81)), /Numeric token/);
  assert.deepEqual(splitGuidanceText("  "), []);
});

test("long displayed text is sent completely in ordered server-sized segments", async () => {
  const old = api.defaults.adapter;
  setup();
  const parts: string[] = [];
  api.defaults.adapter = async (config) => { parts.push(JSON.parse(config.data).text); return response(config); };
  try {
    const text = `${"가".repeat(200)}, 41,000원, 미수금. 이 내용이 맞나요?`;
    await readGuidance(text);
    assert.equal(parts.join(""), text);
    assert.ok(parts.every((part) => Array.from(part).length <= 80));
    assert.equal(plays, parts.length);
    stopGuidanceAudio(); assert.equal(stops, parts.length);
  } finally { stopGuidanceAudio(); api.defaults.adapter = old; }
});

test("long whitespace never creates an empty synthesis request", async () => {
  const old = api.defaults.adapter;
  setup();
  const parts: string[] = [];
  api.defaults.adapter = async (config) => { parts.push(JSON.parse(config.data).text); return response(config); };
  try {
    await readGuidance(`${"가".repeat(79)},${" ".repeat(90)}41,000원`);
    assert.ok(parts.every((part) => part.trim().length > 0));
    assert.equal(parts.join("").replace(/\s/g, ""), `${"가".repeat(79)},41,000원`);
  } finally { stopGuidanceAudio(); api.defaults.adapter = old; }
});

test("cancelling during decode never requests the remaining speech segments", async () => {
  const old = api.defaults.adapter;
  setup();
  let requests = 0;
  let release: (() => void) | undefined;
  decode = () => new Promise((resolve) => { release = () => resolve({} as AudioBuffer); });
  api.defaults.adapter = async (config) => { requests++; return response(config); };
  try {
    const reading = readGuidance(`${"가".repeat(70)}, ${"나".repeat(70)}`);
    await until(() => !!release);
    stopGuidanceAudio();
    // Later decodes must not block the regression test when the stale loop continues.
    decode = async () => ({} as AudioBuffer);
    release!();
    await reading;
    assert.equal(requests, 1);
    assert.equal(plays, 0);
  } finally { release?.(); stopGuidanceAudio(); api.defaults.adapter = old; }
});

test("only STT failure plays the fixed retry cue; device and size failures stay text-only", async () => {
  setup(); const oldFetch = globalThis.fetch;
  let reads = 0, root!: ReactTestRenderer;
  globalThis.fetch = async () => { reads++; return new Response(new Uint8Array([1, 2])); };
  const noop = () => {};
  const screen = (retryCue: boolean, state: "failed" | "processing" = "failed") => <RecordingOverlay state={state} seconds={0} retryCue={retryCue}
    onStop={noop} onCancel={noop} onRetry={noop} onTypeInstead={noop} />;
  try {
    await act(async () => { root = create(screen(false)); });
    assert.equal(reads, 0, "permission and file errors have no retry speech");
    await act(async () => { root.update(screen(true)); });
    assert.equal(reads, 1); assert.equal(plays, 1);
    await act(async () => { root.update(screen(false, "processing")); });
    await act(async () => { root.update(screen(true)); });
    assert.equal(reads, 2, "a second failed recording gets its own fixed retry cue");
  } finally { act(() => root.unmount()); globalThis.fetch = oldFetch; stopGuidanceAudio(); }
});

for (const action of ["continue", "close"] as const) {
  test(`non-sale guidance reads the server message and discards late audio on ${action}`, async () => {
    const old = api.defaults.adapter;
    setup();
    useAuthStore.getState().setCookieSession("audio-owner");
    useCandidateStore.getState().setCandidate(null);
    let release = () => {};
    let requested = false;
    let signal: AbortSignal | undefined;
    const message = "남은 외상은 받을 돈 탭에서 볼 수 있어요.";
    api.defaults.adapter = async (config) => {
      if (config.url?.endsWith("/voice/speak")) {
        assert.deepEqual(JSON.parse(config.data), { text: message });
        requested = true; signal = config.signal as AbortSignal;
        return new Promise((resolve) => { release = () => resolve(response(config)); });
      }
      return { config, status: 200, statusText: "OK", headers: {}, data: config.url?.endsWith("/unpaid")
        ? { total_unpaid: 0, counterparties: [] } : { entries: [], daily_credit: [] } };
    };
    const router = createMemoryRouter([{ path: "/record", element: <RecordPage /> }], { initialEntries: ["/record"] });
    let root!: ReactTestRenderer;
    try {
      await act(async () => { root = create(<RouterProvider router={router} />); });
      await act(async () => {
        root.root.findByType(VoiceRecorder).props.onFailure({ kind: "input", errorCode: "NOT_A_SALE", redirect: "inquiry", message });
      });
      await until(() => requested);
      await act(async () => { root.root.findByType(NonSaleNotice).props[action === "continue" ? "onContinue" : "onClose"](); });
      assert.equal(signal?.aborted, true);
      release();
      await act(async () => { await new Promise((resolve) => setTimeout(resolve, 0)); });
      assert.equal(plays, 0);
      assert.equal(root.root.findAllByType(NonSaleNotice).length, 0);
    } finally {
      release(); act(() => root?.unmount()); router.dispose(); stopGuidanceAudio(); api.defaults.adapter = old;
    }
  });
}


test("resuming a saved clarification primes server audio after the webview output was closed", async () => {
  const old = api.defaults.adapter;
  setup();
  useAuthStore.getState().setCookieSession("repair-audio-owner");
  useCandidateStore.getState().setCandidate(null);
  const draft = { ...createCandidateDraft(snapshot), candidateId: null, credit: null };
  saveRepairDraft({ owner: "repair-audio-owner", draft, utterance: snapshot.utterance_text, transcript: null,
    progress: { field: "credit" }, failure: { kind: "input", errorCode: "MISSING_REQUIRED_FIELD", message: "결제 상태를 확인해 주세요.", problemFields: ["credit"] } });
  window.dispatchEvent(new Event("pagehide"));
  const previousOutput = AudioOutput.latest;
  let requested = false;
  api.defaults.adapter = async (config) => {
    if (config.url?.endsWith("/voice/speak")) { requested = true; return response(config); }
    return { config, status: 200, statusText: "OK", headers: {}, data: config.url?.endsWith("/unpaid")
      ? { total_unpaid: 0, counterparties: [] } : { entries: [], daily_credit: [] } };
  };
  const router = createMemoryRouter([{ path: "/record", element: <RecordPage /> }], { initialEntries: ["/record"] });
  let root!: ReactTestRenderer;
  try {
    await act(async () => { root = create(<RouterProvider router={router} />); });
    await act(async () => { root.root.findAllByType("button").find((button) => button.children.includes("이어서 입력하기"))!.props.onClick(); });
    assert.notEqual(AudioOutput.latest, previousOutput, "the restore tap must open a fresh output");
    await until(() => requested && plays === 1);
    assert.equal(systemReads, 0);
    assert.equal(useCandidateStore.getState().candidate, null);
  } finally {
    await act(async () => { root?.unmount(); }); router.dispose(); clearRepairDraft(); stopGuidanceAudio(); api.defaults.adapter = old;
  }
});


test("confirmation reads the heard utterance, falls back for blank input, and keeps questions unchanged", () => {
  const draft = createCandidateDraft(snapshot);
  const heard = "푸른마트에 계란 다섯 판 삼만원 외상";
  assert.equal(guidanceSummary(draft, undefined, heard), heard + ". 이렇게 들었어요. 화면 내용이 맞으면 장부 확정을 눌러 주세요.");
  assert.equal(guidanceSummary(draft, undefined, "   "), guidanceSummary(draft));
  assert.equal(guidanceSummary(draft, "몇 단이에요?", heard), "몇 단이에요?");
});


for (const text of [
  "가".repeat(70) + ", 41,000원. 화면 내용을 확인해 주세요.",
  "가".repeat(76) + "41,000원 확인해 주세요.",
  "🙂".repeat(75) + "123.45원 확인해 주세요.",
  "긴 설명입니다. ".repeat(20) + "30,000원, 미수금.",
]) {
  test("long speech preserves numeric tokens and all text: " + Array.from(text).length, async () => {
    const old = api.defaults.adapter; setup();
    const parts: string[] = [];
    api.defaults.adapter = async config => { parts.push(JSON.parse(config.data).text); return response(config); };
    try {
      await readGuidance(text);
      assert.equal(parts.join(""), text);
      assert.ok(parts.every(part => Array.from(part).length <= 80));
      for (const token of text.match(/\d[\d,.]*원?/g) ?? []) assert.ok(parts.some(part => part.includes(token)), token);
      if (text.startsWith("가".repeat(70) + ",")) assert.equal(parts[0], "가".repeat(70) + ", 41,000원.");
    } finally { stopGuidanceAudio(); api.defaults.adapter = old; }
  });
}


test("only the first clarification reads the visible transcript across three questions", async () => {
  const old = api.defaults.adapter; setup();
  useAuthStore.getState().setCookieSession("question-owner");
  const heard = "푸른마트 계란 다섯 판 외상";
  const requests: string[] = [];
  api.defaults.adapter = async config => { requests.push(JSON.parse(config.data).text); return response(config); };
  function Questions() {
    const [draft, setDraft] = useState({ ...createCandidateDraft(snapshot), credit: null as boolean | null, total_amount: "", items: [{ ...createCandidateDraft(snapshot).items[0], qty: "" }] });
    return <ClarificationCard draft={draft} transcript={heard} voiceFields={[]} busy={false} onChange={setDraft} onFinish={() => {}} onClose={() => {}} />;
  }
  let root!: ReactTestRenderer;
  const next = () => root.root.findAllByType("button").find(button => button.children.includes("다음 질문"))!.props.onClick();
  try {
    await act(async () => { root = create(<Questions />); });
    assert.equal(requests[0], heard + ". 외상이에요, 현금이에요?");
    await act(async () => { root.root.findByType(ClarifyCard).props.onAnswer(true); });
    await act(async () => { next(); });
    assert.equal(requests[1], "배추 몇 박스이에요?");
    await act(async () => { root.root.findByType(ClarifyCard).props.onValueChange("5"); });
    await act(async () => { next(); });
    assert.equal(requests[2], "전부 얼마예요?");
    assert.equal(requests.filter(text => text.includes(heard)).length, 1);
    assert.equal(guidanceSummary(createCandidateDraft(snapshot), "전부 얼마예요?", "  ", true), "전부 얼마예요?");
  } finally { act(() => root?.unmount()); stopGuidanceAudio(); api.defaults.adapter = old; }
});
