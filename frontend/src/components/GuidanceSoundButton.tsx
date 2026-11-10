import { useSyncExternalStore } from "react";
import { Volume2, VolumeX } from "lucide-react";
import { guidanceSoundEnabled, prepareGuidanceAudio, saveGuidanceSoundEnabled, subscribeGuidanceSound } from "../utils/guidanceAudio";

/** One device-wide mute control, separate from the question/summary replay action. */
export function GuidanceSoundButton() {
  const enabled = useSyncExternalStore(subscribeGuidanceSound, guidanceSoundEnabled, () => true);
  const Icon = enabled ? Volume2 : VolumeX;
  return <button type="button" aria-pressed={!enabled} onClick={() => {
    saveGuidanceSoundEnabled(!enabled);
    if (!enabled) prepareGuidanceAudio();
  }}
    className="flex min-h-12 items-center gap-space-2 rounded-xl px-3 text-caption text-text-sub active:bg-bg">
    <Icon className="h-5 w-5" aria-hidden />{enabled ? "소리 끄기" : "소리 켜기"}
  </button>;
}
