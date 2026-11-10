import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { VoiceRecorder } from "../src/components/VoiceRecorder";
import { ClarificationCard } from "../src/components/ClarificationCard";
import { ClarifyCard } from "../src/ui/ClarifyCard";
import { createCandidateDraft } from "../src/utils/candidateDraft";
import { api } from "../src/api/client";
import { saveRecordingCueEnabled } from "../src/utils/recordingCue";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { snapshot, until } from "./review-fixture";

test("a delayed spoken quantity answer cannot fill a new session's repair form", async () => {
  const previous = api.defaults.adapter;
  useAuthStore.getState().setCookieSession("answer-owner", ["qty"]);
  const draft = createCandidateDraft({ ...snapshot, items: [{ ...snapshot.items[0], qty: null }] });
  let release: (() => void) | undefined, changes = 0, root!: ReactTestRenderer;
  api.defaults.adapter = (config) => new Promise((resolve) => { release = () => resolve({ config, status: 200, statusText: "OK", headers: {},
    data: { field: "qty", status: "ok", value: 3, unit: null, utterance_text: "셋이요" } }); });
  try {
    act(() => { root = create(<ClarificationCard draft={draft} busy={false} onChange={() => changes++} onFinish={() => {}} onClose={() => {}} />); });
      // Inject the answer directly; the recorder is mounted before the start gesture.
    let answer!: Promise<void>;
    await act(async () => { answer = root.root.findByType(VoiceRecorder).props.onAnswer(new Blob(["synthetic answer"])); await until(() => !!release); });
    useAuthStore.getState().setCookieSession("answer-owner", ["qty"]);
    await act(async () => { release!(); await answer; });
    assert.equal(changes, 0);
  } finally { act(() => root?.unmount()); release?.(); api.defaults.adapter = previous; }
});

for (const mode of ["current-session", "new-session", "new-session-error", "new-candidate-error", "unmounted-new-session", "unmounted-current-session"] as const) {
  test(`voice response remains with the session that submitted it: ${mode}`, async () => {
    const adapter = api.defaults.adapter;
    const navigatorDescriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
    const recorderDescriptor = Object.getOwnPropertyDescriptor(globalThis, "MediaRecorder");
    localStorage.clear(); sessionStorage.clear(); saveRecordingCueEnabled(false);
    useAuthStore.getState().setCookieSession("voice-owner");
    useCandidateStore.getState().setCandidate(null);
    Object.defineProperty(globalThis, "navigator", { configurable: true, value: {
      mediaDevices: { getUserMedia: async () => ({ getTracks: () => [{ stop() {} }] }) },
    } });
    class Recorder {
      static isTypeSupported() { return true; }
      state = "inactive";
      mimeType = "audio/webm";
      ondataavailable: ((event: { data: Blob }) => void) | null = null;
      onstop: (() => void) | null = null;
      start() { this.state = "recording"; }
      stop() { this.state = "inactive"; this.ondataavailable?.({ data: new Blob(["synthetic voice"]) }); this.onstop?.(); }
    }
    Object.defineProperty(globalThis, "MediaRecorder", { configurable: true, value: Recorder });
    let release: (() => void) | undefined, root: ReactTestRenderer | undefined;
    const accepted: string[] = [], failures: string[] = [];
    api.defaults.adapter = (config) => new Promise((resolve, reject) => { release = () => mode.endsWith("error")
      ? reject(new Error("old offline request"))
      : resolve({ config, status: 200, statusText: "OK", headers: {}, data: { ...snapshot, status: "candidate", parser_type: "llm" } }); });
    const click = (label: string) => root!.root.findAllByType("button").find((node) => node.children.join("") === label)!.props.onClick();
    try {
      await act(async () => { root = create(<VoiceRecorder disabled={false} ensureAuth={async () => {}} onBusy={() => {}}
        onFallback={() => {}} onCandidate={(candidate) => accepted.push(candidate.entry_id)} onFailure={(error) => failures.push(error.message)} />); });
      await act(async () => click("녹음 시작"));
      await act(async () => click("녹음 종료"));
      await act(async () => { click("녹음으로 후보 만들기"); await until(() => !!release); });
      if (mode.startsWith("unmounted")) { act(() => root!.unmount()); root = undefined; }
      if (mode === "new-candidate-error") useCandidateStore.getState().setCandidate({ ...snapshot, entry_id: "new-work" });
      else if (mode !== "unmounted-current-session" && mode !== "current-session") useAuthStore.getState().setCookieSession("voice-owner");
      await act(async () => release!());
      assert.deepEqual(accepted, mode === "current-session" ? [snapshot.entry_id] : []);
      assert.deepEqual(failures, [], "stale failures cannot replace the current session's screen");
      assert.equal(useCandidateStore.getState().candidate?.entry_id ?? null,
        mode === "unmounted-current-session" ? snapshot.entry_id : mode === "new-candidate-error" ? "new-work" : null);
    } finally {
      act(() => root?.unmount()); release?.(); api.defaults.adapter = adapter;
      if (navigatorDescriptor) Object.defineProperty(globalThis, "navigator", navigatorDescriptor); else Reflect.deleteProperty(globalThis, "navigator");
      if (recorderDescriptor) Object.defineProperty(globalThis, "MediaRecorder", recorderDescriptor); else Reflect.deleteProperty(globalThis, "MediaRecorder");
      useCandidateStore.getState().setCandidate(null); localStorage.clear(); sessionStorage.clear();
    }
  });
}
