import { api, currentSessionEpoch, sessionRequestConfig } from "../api/client";
import { localStore } from "./browserStorage";
import { splitGuidanceText } from "./guidanceText";

const SETTING_KEY = "echobill_guidance_sound";
const listeners = new Set<() => void>();
let context: AudioContext | null = null;
let outputReady = Promise.resolve();
const sources = new Set<AudioBufferSourceNode>();
let pending: AbortController | null = null;
let generation = 0;
let listening = false;

/** Release browser output even when an interrupted source rejects stop(). */
function releasePlayback(playing: AudioBufferSourceNode): void {
  playing.onended = null;
  try {
    playing.stop();
  } catch (error) {
    // An already stopped output is the only expected cancellation failure.
    if (!(error instanceof DOMException && error.name === "InvalidStateError")) throw error;
  } finally {
    playing.disconnect();
  }
}

/** Disconnect every scheduled segment before surfacing the first unexpected output error. */
function releaseScheduledPlayback(): void {
  const playing = [...sources];
  sources.clear();
  const failures: unknown[] = [];
  for (const output of playing) {
    try { releasePlayback(output); }
    catch (error) { failures.push(error); }
  }
  if (failures.length) throw failures[0];
}

/** Only the sound preference is persisted; spoken customer data stays in memory. */
export const guidanceSoundEnabled = () => localStore.getItem(SETTING_KEY) !== "off";
export function subscribeGuidanceSound(listener: () => void) {
  listeners.add(listener);
  return () => { listeners.delete(listener); };
}

/** Invalidate pending synthesis as well as audio already playing. */
export function stopGuidanceAudio(): void {
  generation++;
  pending?.abort();
  pending = null;
  try {
    releaseScheduledPlayback();
  } finally {
    if (typeof window !== "undefined" && "speechSynthesis" in window) window.speechSynthesis.cancel();
  }
}

/** A single mute setting applies to failures, questions and confirmation summaries. */
export function saveGuidanceSoundEnabled(enabled: boolean): void {
  localStore.setItem(SETTING_KEY, enabled ? "on" : "off");
  if (!enabled) stopGuidanceAudio();
  listeners.forEach((listener) => listener());
}

/** Discard the device interrupted by iOS screen lock; the next tap opens a fresh one. */
function resetGuidanceOutput(): void {
  try { stopGuidanceAudio(); }
  finally { closeGuidanceOutput(); }
}

/** Closing optional audio must not prevent the next input gesture. */
function closeGuidanceOutput(): void {
  const previous = context;
  context = null;
  outputReady = Promise.resolve();
  if (previous && previous.state !== "closed") void previous.close().catch(() => undefined);
}

/** Observe exits and screen unlock even when only the iOS system fallback is available. */
function observeGuidanceLifecycle(): void {
  if (!listening) {
    listening = true;
    window.addEventListener("pagehide", resetGuidanceOutput);
    window.addEventListener("pageshow", resetGuidanceOutput);
    window.addEventListener("echobill:session-ended", stopGuidanceAudio);
    window.addEventListener("echobill:session-changed", stopGuidanceAudio);
    document.addEventListener("visibilitychange", () => {
      if (document.visibilityState === "hidden") stopGuidanceAudio();
      else resetGuidanceOutput();
    });
  }
}

/** Unlock Web Audio during the recording/replay tap, before microphone or network awaits. */
export function prepareGuidanceAudio(): void {
  stopGuidanceAudio();
  if (!guidanceSoundEnabled()) return;
  observeGuidanceLifecycle();
  primeGuidanceOutput();
}

/** Prime a fresh output without invalidating the current speech request. */
function primeGuidanceOutput(): void {
  try {
    const Audio = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (!Audio) return;
    if (context && context.state !== "running") closeGuidanceOutput();
    if (!context) context = new Audio();
    const resumed = context.resume();
    // resume() is asynchronous even in a tap; a blocked output must not hold the form.
    outputReady = new Promise<void>((resolve) => {
      const timer = window.setTimeout(resolve, 1000);
      const finish = () => { window.clearTimeout(timer); resolve(); };
      void resumed.then(finish, finish);
    });
    const silent = context.createBufferSource();
    silent.buffer = context.createBuffer(1, 1, context.sampleRate);
    silent.connect(context.destination);
    silent.onended = () => silent.disconnect();
    silent.start();
  } catch { closeGuidanceOutput(); } // Unsupported/blocked audio falls back to text.
}

/** iOS alone can fall back to the existing system voice; Android stays text-only. */
function systemVoice(text: string): void {
  const ios = /iPad|iPhone|iPod/.test(navigator.userAgent) || navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1;
  if (!ios || !("speechSynthesis" in window) || typeof SpeechSynthesisUtterance === "undefined") return;
  try {
    const utterance = new SpeechSynthesisUtterance(text);
    utterance.lang = "ko-KR";
    window.speechSynthesis.speak(utterance);
  } catch { /* Optional system speech may be blocked by the webview. */ }
}

/** Play fixed failure audio or synthesize a short question; failures never change the form. */
export async function readGuidance(text: string, fixedUrl?: string): Promise<void> {
  stopGuidanceAudio();
  if (!guidanceSoundEnabled() || document.visibilityState === "hidden") return;
  observeGuidanceLifecycle();
  const ticket = generation;
  const epoch = currentSessionEpoch();
  const controller = new AbortController();
  pending = controller;
  const current = () => ticket === generation && epoch === currentSessionEpoch() && !controller.signal.aborted && guidanceSoundEnabled();
  try {
    await outputReady;
    if (!current()) return;
    if (context && context.state !== "running") {
      primeGuidanceOutput();
      await outputReady;
      if (!current()) return;
    }
    if (!context || context.state !== "running") throw new Error("Audio output unavailable");
    const parts = fixedUrl ? [text] : splitGuidanceText(text).filter((part) => part.trim().length > 0);
    const buffers: AudioBuffer[] = [];
    for (const part of parts) {
      let bytes: ArrayBuffer;
      if (fixedUrl) {
        const response = await fetch(fixedUrl, { signal: controller.signal });
        if (!response.ok) throw new Error("Guidance file unavailable");
        bytes = await response.arrayBuffer();
      } else {
        const response = await api.post<Blob>("/api/v1/voice/speak", { text: part }, {
          ...sessionRequestConfig(), signal: controller.signal, timeout: 12_000, responseType: "blob",
        });
        bytes = await response.data.arrayBuffer();
      }
      if (!current()) return;
      if (!context) return;
      const buffer = await context.decodeAudioData(bytes);
      if (!current()) return;
      buffers.push(buffer);
    }
    if (!current()) return;
    if (context?.state !== "running") {
      primeGuidanceOutput();
      await outputReady;
      if (!current()) return;
    }
    const audio = context;
    if (!audio || audio.state !== "running") throw new Error("Audio output interrupted");
    let startAt = audio.currentTime;
    for (const buffer of buffers) {
      const next = audio.createBufferSource();
      next.buffer = buffer;
      next.connect(audio.destination);
      next.onended = () => { next.disconnect(); sources.delete(next); };
      try {
        next.start(startAt);
        startAt += buffer.duration;
      } catch (error) {
        next.onended = null;
        next.disconnect();
        throw error;
      }
      sources.add(next);
    }
  } catch {
    // No customer text, tokens or synthesis errors are logged on this optional path.
    if (current()) {
      releaseScheduledPlayback();
      systemVoice(text);
    }
  } finally {
    if (pending === controller) pending = null;
  }
}
