import { test } from "node:test";
import assert from "node:assert/strict";
import { createRef } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { renderToStaticMarkup } from "react-dom/server";
import { VoiceRecorder, type VoiceRecorderHandle } from "../src/components/VoiceRecorder";
import { RecordingOverlay } from "../src/ui/RecordingOverlay";
import { MyPageView } from "../src/ui/MyPageView";
import { prepareRecordingCue, recordingCueEnabled, saveRecordingCueEnabled } from "../src/utils/recordingCue";

import { saveGuidanceSoundEnabled } from "../src/utils/guidanceAudio";
import { saveAutoStopEnabled } from "../src/utils/silenceDetection";

const values = new Map<string, string>();
Object.assign(globalThis, {
  localStorage: { getItem: (key: string) => values.get(key) ?? null, setItem: (key: string, value: string) => values.set(key, value) },
  window: Object.assign(new EventTarget(), { setTimeout, clearTimeout, setInterval, clearInterval }),
  document: Object.assign(new EventTarget(), { visibilityState: "visible" }),
});

/** Hold synthetic output open to prove that recording never captures the start tone. */
class FakeAudioContext {
  static instances: FakeAudioContext[] = [];
  static mode = "running";
  state = "suspended";
  currentTime = 0;
  sampleRate = 48000;
  destination = {};
  onended: (() => void) | null = null;
  toneStarted = false;
  disconnected = false;
  constructor() { FakeAudioContext.instances.push(this); }
  resume() {
    if (FakeAudioContext.mode === "rejected") return Promise.reject(new DOMException("blocked", "NotAllowedError"));
    if (FakeAudioContext.mode === "blocked") return new Promise<void>(() => {});
    this.state = "running"; return Promise.resolve();
  }
  close() { this.state = "closed"; return Promise.resolve(); }
  createBuffer() { return {}; }
  createBufferSource() { return { connect() {}, disconnect() {}, start() {}, buffer: null, onended: null }; }
  createOscillator() {
    const owner = this;
    return { frequency: { setValueAtTime() {} }, connect() {}, disconnect() { owner.disconnected = true; },
      start() { owner.toneStarted = true; }, stop() {},
      set onended(value: () => void) { owner.onended = value; } };
  }
  createGain() { return { gain: { setValueAtTime() {}, linearRampToValueAtTime() {} }, connect() {}, disconnect() {} }; }
}

/** Browser lifecycle fixture with manually resolved microphone permission. */
async function fixture(sound = true, answer = false) {
  values.clear(); saveRecordingCueEnabled(sound);
  saveGuidanceSoundEnabled(sound);
  FakeAudioContext.instances = []; FakeAudioContext.mode = "running";
  Object.assign(window, { AudioContext: FakeAudioContext });
  Object.assign(document, { visibilityState: "visible" });
  let grant!: (stream: MediaStream) => void, deny!: (error: Error) => void;
  const counts = { stopped: 0, recordings: 0, requests: 0, vibrations: 0, fallbacks: 0 };
  const stream = { getTracks: () => [{ stop: () => { counts.stopped++; } }] } as unknown as MediaStream;
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: {
    mediaDevices: { getUserMedia: () => { counts.requests++; return new Promise<MediaStream>((yes, no) => { grant = yes; deny = no; }); } },
    vibrate: () => { counts.vibrations++; return true; },
  } });
  class Recorder {
    static isTypeSupported() { return true; }
    state = "inactive";
    start() { this.state = "recording"; counts.recordings++; }
    stop() { this.state = "inactive"; }
  }
  Object.assign(globalThis, { MediaRecorder: Recorder });
  const control = createRef<VoiceRecorderHandle>();
  let root!: ReactTestRenderer;
  await act(async () => { root = create(<VoiceRecorder controlRef={control} onAnswer={answer ? async () => {} : undefined} onCandidate={() => {}}
    onFailure={() => {}} onFallback={() => { counts.fallbacks++; }} ensureAuth={async () => {}} onBusy={() => {}} disabled={false} />); });
  return { root, control, counts, grant: () => grant(stream), deny: () => deny(new DOMException("denied", "NotAllowedError")),
    close: async () => { await act(async () => root.unmount()); } };
}

test("fast permission skips help; recorder waits for tone end and output tail", async () => {
  const f = await fixture();
  try {
    act(() => f.control.current!.start());
    assert.equal(FakeAudioContext.instances.length, 2, "unlock guidance audio and the short cue within the tap gesture");
    assert.equal(f.root.root.findAllByType(RecordingOverlay).length, 0);
    await act(async () => f.grant());
    const audio = FakeAudioContext.instances.at(-1)!;
    assert.equal(audio.toneStarted, true);
    assert.equal(f.root.root.findByType(RecordingOverlay).props.state, "recording");
    assert.equal(f.root.root.findByType(RecordingOverlay).props.preparing, true);
    assert.equal(f.counts.recordings, 0); assert.equal(f.counts.vibrations, 1);
    await act(async () => { audio.onended!(); await new Promise((resolve) => setTimeout(resolve, 150)); });
    assert.equal(audio.state, "closed"); assert.equal(f.counts.recordings, 1);
    assert.equal(f.root.root.findByType(RecordingOverlay).props.preparing, false);
  } finally { await f.close(); }
});

test("slow permission shows help after 500 ms; cancellation disposes late approval", async () => {
  const f = await fixture();
  try {
    act(() => f.control.current!.start());
    assert.equal(f.root.root.findAllByType(RecordingOverlay).length, 0);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 530)); });
    assert.equal(f.root.root.findByType(RecordingOverlay).props.state, "requesting");
    act(() => f.root.root.findByType(RecordingOverlay).props.onCancel());
    await act(async () => f.grant());
    assert.equal(f.counts.recordings, 0); assert.equal(f.counts.stopped, 1);
    assert.equal(f.root.root.findAllByType(RecordingOverlay).length, 0);
  } finally { await f.close(); }
});

for (const interruption of ["cancel", "pagehide", "hidden", "unmount"] as const) {
  test(`interrupting the tone via ${interruption} cannot start recording later`, async () => {
    const f = await fixture(); let unmounted = false;
    try {
      act(() => f.control.current!.start()); await act(async () => f.grant());
      const audio = FakeAudioContext.instances.at(-1)!;
      await act(async () => {
        if (interruption === "unmount") { f.root.unmount(); unmounted = true; }
        else if (interruption === "cancel") f.root.root.findByType(RecordingOverlay).props.onCancel();
        else if (interruption === "pagehide") window.dispatchEvent(new Event("pagehide"));
        else { Object.assign(document, { visibilityState: "hidden" }); document.dispatchEvent(new Event("visibilitychange")); }
      });
      assert.equal(audio.state, "closed"); assert.equal(audio.disconnected, true);
      assert.equal(f.counts.recordings, 0); assert.equal(f.counts.stopped, 1);
    } finally { if (!unmounted) await f.close(); }
  });
}

test("disabled cue uses no audio or vibration and double taps open only one microphone", async () => {
  const f = await fixture(false);
  try {
    act(() => { f.control.current!.start(); f.control.current!.start(); }); await act(async () => f.grant());
    assert.equal(f.counts.requests, 1); assert.equal(f.counts.recordings, 1);
    assert.equal(FakeAudioContext.instances.length, 0); assert.equal(f.counts.vibrations, 0);
  } finally { await f.close(); }
});

test("permission denial closes audio and offers text or retry", async () => {
  const f = await fixture();
  try {
    act(() => f.control.current!.start()); await act(async () => f.deny());
    assert.equal(f.counts.recordings, 0); assert.equal(f.counts.fallbacks, 1);
    assert.equal(FakeAudioContext.instances[0].state, "closed");
    assert.equal(f.root.root.findByType(RecordingOverlay).props.state, "failed");
    assert.equal(f.root.root.findByType(RecordingOverlay).props.retryCue, false);
  } finally { await f.close(); }
});

test("voice answers never enable silence analysis even when the preference is on", async () => {
  const f = await fixture(false, true);
  saveAutoStopEnabled(true);
  try {
    act(() => f.control.current!.start()); await act(async () => f.grant());
    assert.equal(f.counts.recordings, 1);
    assert.equal(FakeAudioContext.instances.length, 0);
    assert.equal(f.root.root.findAllByType(RecordingOverlay).length, 0);
    act(() => f.root.root.findAllByType("button").find(button => button.children.includes("녹음 종료"))!.props.onClick());
    assert.equal(f.counts.stopped, 1);
  } finally { await f.close(); saveAutoStopEnabled(false); }
});

test("unavailable silence analysis keeps recording and reports the manual stop fallback", async () => {
  const f = await fixture(false);
  saveAutoStopEnabled(true);
  try {
    act(() => f.control.current!.start()); await act(async () => f.grant());
    assert.equal(f.counts.recordings, 1);
    assert.equal(f.root.root.findByType(RecordingOverlay).props.autoStop, "unavailable");
    act(() => f.root.root.findByType(RecordingOverlay).props.onStop());
    assert.equal(f.counts.stopped, 1);
    assert.equal(FakeAudioContext.instances[0].state, "closed");
  } finally { await f.close(); saveAutoStopEnabled(false); }
});

test("detected speech followed by silence stops the recorder and microphone exactly once", async (t) => {
  const f = await fixture(false);
  saveAutoStopEnabled(true);
  const oldPerformance = Object.getOwnPropertyDescriptor(globalThis, "performance");
  const timers = { setTimeout: window.setTimeout, clearTimeout: window.clearTimeout, setInterval: window.setInterval, clearInterval: window.clearInterval };
  let now = 0, level = 0.1;
  const prototype = FakeAudioContext.prototype as unknown as Record<string, unknown>;
  prototype.createMediaStreamSource = () => ({ connect() {}, disconnect() {} });
  prototype.createAnalyser = () => ({ fftSize: 2048, disconnect() {}, getFloatTimeDomainData: (samples: Float32Array) => samples.fill(level) });
  Object.defineProperty(globalThis, "performance", { configurable: true, value: { now: () => now } });
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"] });
  Object.assign(window, { setTimeout, clearTimeout, setInterval, clearInterval });
  const tick = (duration: number) => { for (let i = 0; i < duration; i += 100) { now += 100; t.mock.timers.tick(100); } };
  try {
    act(() => f.control.current!.start()); await act(async () => f.grant());
    assert.equal(f.root.root.findByType(RecordingOverlay).props.autoStop, "active");
    await act(async () => tick(1000));
    assert.equal(f.counts.stopped, 0);
    level = 0;
    await act(async () => tick(2900)); assert.equal(f.counts.stopped, 0);
    await act(async () => tick(100)); assert.equal(f.counts.stopped, 1);
    await act(async () => tick(5000)); assert.equal(f.counts.stopped, 1);
    assert.equal(f.counts.recordings, 1);
    assert.equal(FakeAudioContext.instances[0].state, "closed");
    assert.equal(f.root.root.findAllByType(RecordingOverlay).length, 0);
  } finally {
    await f.close(); saveAutoStopEnabled(false);
    delete prototype.createMediaStreamSource; delete prototype.createAnalyser;
    Object.assign(window, timers); t.mock.timers.reset();
    if (oldPerformance) Object.defineProperty(globalThis, "performance", oldPerformance);
  }
});

for (const reason of ["unsupported", "blocked", "rejected"] as const) {
  test(`${reason} audio still allows recording with visual guidance`, async () => {
    const f = await fixture();
    try {
      if (reason === "unsupported") Object.assign(window, { AudioContext: undefined });
      else FakeAudioContext.mode = reason;
      act(() => f.control.current!.start());
      await act(async () => { f.grant(); await new Promise((resolve) => setTimeout(resolve, reason === "blocked" ? 1100 : 0)); });
      assert.equal(f.counts.recordings, 1); assert.equal(f.counts.fallbacks, 0);
    } finally { await f.close(); }
  });
}

test("a cancelled cue cannot play after permission resolves", async () => {
  const f = await fixture();
  try {
    const cue = prepareRecordingCue(true); cue.cancel(); let shown = false;
    await cue.play(new AbortController().signal, () => { shown = true; });
    assert.equal(shown, false); assert.equal(FakeAudioContext.instances[0].toneStarted, false);
  } finally { await f.close(); }
});

test("missing tone-end event silences output before allowing recording", async () => {
  const f = await fixture();
  try {
    act(() => f.control.current!.start()); await act(async () => f.grant());
    assert.equal(f.counts.recordings, 0);
    await act(async () => { await new Promise((resolve) => setTimeout(resolve, 830)); });
    assert.equal(FakeAudioContext.instances[0].state, "closed");
    assert.equal(FakeAudioContext.instances[0].disconnected, true);
    assert.equal(f.counts.recordings, 1);
  } finally { await f.close(); }
});

test("recording timer begins after the cue and still automatically stops at 30 seconds", async (t) => {
  const f = await fixture(false);
  const timers = { setTimeout: window.setTimeout, clearTimeout: window.clearTimeout,
    setInterval: window.setInterval, clearInterval: window.clearInterval };
  t.mock.timers.enable({ apis: ["setTimeout", "setInterval", "Date"] });
  Object.assign(window, { setTimeout, clearTimeout, setInterval, clearInterval });
  try {
    act(() => f.control.current!.start()); await act(async () => f.grant());
    await act(async () => { t.mock.timers.tick(29_000); });
    assert.equal(f.root.root.findByType(RecordingOverlay).props.seconds, 29);
    assert.equal(f.counts.stopped, 0);
    await act(async () => { t.mock.timers.tick(1000); });
    assert.equal(f.root.root.findAllByType(RecordingOverlay).length, 0);
    assert.equal(f.counts.stopped, 1);
  } finally { await f.close(); Object.assign(window, timers); t.mock.timers.reset(); }
});

test("hint changes once at three seconds outside live announcements", () => {
  const noop = () => {};
  for (const seconds of [0, 2, 3, 10]) {
    const html = renderToStaticMarkup(<RecordingOverlay state="recording" seconds={seconds}
      onStop={noop} onCancel={noop} onRetry={noop} onTypeInstead={noop} />);
    assert.match(html, seconds < 3 ? /가게 이름부터 또박또박 말해 주세요/ : /거래처, 품목, 금액, 외상인지 말해 주세요/);
    assert.match(html, /aria-live="off"/);
    assert.doesNotMatch(html, seconds < 3 ? /거래처, 품목, 금액, 외상인지/ : /가게 이름부터 또박또박/);
  }
});

test("notification preference persists and uses the shared settings switch", () => {
  values.clear(); assert.equal(recordingCueEnabled(), true);
  saveRecordingCueEnabled(false); assert.equal(recordingCueEnabled(), false);
  saveRecordingCueEnabled(true); assert.equal(recordingCueEnabled(), true);
  const noop = () => {};
  const html = renderToStaticMarkup(<MyPageView business={null} aiConsent={false} footnote="test"
    recordingCue={false} onRecordingCueChange={noop} onAiConsentChange={noop} onBack={noop} onEditBusiness={noop}
    onOpenPrivacy={noop} onContact={noop} onLogout={noop} onRequestWithdrawal={noop} />);
  assert.match(html, /녹음 시작 알림/); assert.match(html, /aria-checked="false"/);
});

test("the start cue reopens an interrupted device once and waits for its tone before recording", async () => {
  FakeAudioContext.instances = []; FakeAudioContext.mode = "running";
  let first = true, ready = 0;
  class RecoveringAudio extends FakeAudioContext {
    resume() {
      if (!first) return super.resume();
      first = false; this.state = "interrupted"; return Promise.resolve();
    }
  }
  Object.assign(window, { AudioContext: RecoveringAudio });
  const cue = prepareRecordingCue(true);
  const pending = cue.play(new AbortController().signal, () => { ready++; });
  try {
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(FakeAudioContext.instances.length, 2);
    assert.equal(FakeAudioContext.instances[0].state, "closed");
    const output = FakeAudioContext.instances[1];
    assert.equal(output.toneStarted, true); assert.equal(ready, 1);
    let finished = false;
    void pending.then(() => { finished = true; });
    assert.equal(finished, false, "microphone start still waits for the audible cue to finish");
    output.onended!();
    await pending;
    assert.equal(output.state, "closed");
  } finally { cue.cancel(); }
});
