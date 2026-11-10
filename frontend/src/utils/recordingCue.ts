import { localStore } from "./browserStorage";

const SETTING_KEY = "echobill_recording_cue";

/** Device-only sound/haptic preference; contains no account or recording data. */
export function recordingCueEnabled(): boolean {
  return localStore.getItem(SETTING_KEY) !== "off";
}

/** Remember the preference for the next recording on this device. */
export function saveRecordingCueEnabled(enabled: boolean): void {
  localStore.setItem(SETTING_KEY, enabled ? "on" : "off");
}

export interface RecordingCue {
  play: (signal: AbortSignal, onReady: () => void) => Promise<void>;
  cancel: () => void;
}

/** Optional haptics: blocked or unsupported webviews report no vibration. */
function vibrateRecordingCue(): boolean {
  try { return navigator.vibrate?.(35) ?? false; }
  catch { return false; }
}

/** Prime Web Audio in the tap gesture, then play only once the microphone is ready.
 * Optional audio failures fall back to the visual cue; cancellation always silences it.
 */
export function prepareRecordingCue(enabled: boolean): RecordingCue {
  let context: AudioContext | null = null;
  let oscillator: OscillatorNode | null = null;
  let gain: GainNode | null = null;
  let cancelled = false;
  let finishPlayback: (() => void) | undefined;
  const close = () => {
    oscillator?.disconnect(); gain?.disconnect();
    oscillator = null; gain = null;
    if (context && context.state !== "closed") {
      // Closing an already interrupted audio device can reject; sound is optional.
      void context.close().catch(() => undefined);
    }
    context = null;
  };
  /** Reopen once if iOS leaves the tapped output suspended or interrupted. */
  const prime = (): Promise<void> => {
    try {
      const Audio = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
      if (Audio) {
        context = new Audio();
        const audio = context;
        // Call resume before the first await so iOS keeps the button's user activation.
        const resumed = audio.resume();
        const ready = new Promise<void>((resolve) => {
          const timeout = window.setTimeout(resolve, 500);
          void resumed.then(() => { window.clearTimeout(timeout); resolve(); }, () => {
            window.clearTimeout(timeout); resolve();
          });
        });
        const silent = audio.createBufferSource();
        silent.buffer = audio.createBuffer(1, 1, audio.sampleRate);
        silent.connect(audio.destination);
        silent.onended = () => silent.disconnect();
        silent.start();
        return ready;
      }
    } catch { close(); } // Unsupported/blocked audio must not prevent microphone use.
    return Promise.resolve();
  };
  let recovered = false;
  const ready = enabled ? prime() : Promise.resolve();
  return {
    cancel: () => { cancelled = true; close(); finishPlayback?.(); },
    play: async (signal, onReady) => {
      await ready;
      if (cancelled || signal.aborted) return;
      if (enabled && context && context.state !== "running" && !recovered) {
        recovered = true;
        close();
        await prime();
        if (cancelled || signal.aborted) return;
      }
      onReady();
      if (enabled) vibrateRecordingCue();
      const audio = context;
      if (!audio || audio.state !== "running") { close(); return; }
      await new Promise<void>((resolve) => {
        let settled = false;
        const finish = () => {
          if (settled) return;
          settled = true;
          window.clearTimeout(timeout);
          signal.removeEventListener("abort", abort);
          close(); finishPlayback = undefined;
          resolve();
        };
        const abort = () => { cancelled = true; finish(); };
        const timeout = window.setTimeout(finish, 800);
        finishPlayback = finish;
        signal.addEventListener("abort", abort, { once: true });
        try {
          oscillator = audio.createOscillator(); gain = audio.createGain();
          oscillator.type = "sine";
          oscillator.frequency.setValueAtTime(880, audio.currentTime);
          gain.gain.setValueAtTime(0, audio.currentTime);
          gain.gain.linearRampToValueAtTime(0.12, audio.currentTime + 0.015);
          gain.gain.linearRampToValueAtTime(0, audio.currentTime + 0.16);
          oscillator.connect(gain); gain.connect(audio.destination);
          // Allow the output buffer and speaker tail to drain before recording.
          oscillator.onended = () => { close(); window.clearTimeout(timeout); window.setTimeout(finish, 120); };
          oscillator.start(); oscillator.stop(audio.currentTime + 0.18);
        } catch { finish(); } // A suspended or unavailable output uses the visual cue.
      });
    },
  };
}
