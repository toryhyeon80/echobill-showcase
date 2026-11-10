import { localStore } from "./browserStorage";

const key = "echobill_auto_stop_recording";
/** Real-device calibration is pending; keep off until explicitly enabled on this device. */
export const autoStopEnabled = () => localStore.getItem(key) === "on";
export const saveAutoStopEnabled = (enabled: boolean) => localStore.setItem(key, enabled ? "on" : "off");

/** Provisional energy gate: require speech first, then 3 seconds of silence and 4 seconds total. */
export function createSilenceDetector(startedAt: number) {
  let speechSince: number | null = null, lastSpeech = startedAt, lastSample = startedAt;
  let heardSpeech = false, ended = false;
  return (rms: number, now: number): boolean => {
    if (ended || !Number.isFinite(rms) || rms < 0 || !Number.isFinite(now) || now < lastSample) return false;
    lastSample = now;
    if (rms >= 0.02) {
      speechSince ??= now;
      if (now - speechSince >= 300) heardSpeech = true;
      lastSpeech = now;
    } else speechSince = null;
    if (heardSpeech && now - startedAt >= 4000 && now - lastSpeech >= 3000) { ended = true; return true; }
    return false;
  };
}
