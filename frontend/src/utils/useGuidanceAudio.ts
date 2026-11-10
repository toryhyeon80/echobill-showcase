import { useEffect, useRef, useSyncExternalStore } from "react";
import { guidanceSoundEnabled, readGuidance, stopGuidanceAudio, subscribeGuidanceSound } from "./guidanceAudio";

/** Read once per screen/question, not for every keystroke; dispose late responses on exit. */
export function useGuidanceAudio(text: string, identity: string, active = true, fixedUrl?: string): void {
  const latest = useRef(text);
  const lastRead = useRef<string | null>(null);
  latest.current = text;
  const enabled = useSyncExternalStore(subscribeGuidanceSound, guidanceSoundEnabled, () => true);
  useEffect(() => {
    if (!active && fixedUrl) lastRead.current = null;
    let cancelled = false;
    // StrictMode probes effects before paint; only the live effect should consume this identity.
    queueMicrotask(() => {
      if (!cancelled && active && enabled && latest.current && lastRead.current !== identity) {
        lastRead.current = identity;
        void readGuidance(latest.current, fixedUrl);
      }
    });
    return () => { cancelled = true; stopGuidanceAudio(); };
  }, [identity, active, enabled, fixedUrl]);
}
