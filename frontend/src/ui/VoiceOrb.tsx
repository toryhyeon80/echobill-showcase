import { VOICE_GRADIENT } from "./voiceGradient";

interface VoiceOrbProps {
  /**
   * Live mic level 0–1 (RMS from a Web Audio AnalyserNode on the real page).
   * Omitted → a soft breathing animation (preview, or when the analyser is unavailable).
   */
  level?: number;
}

// Diameters (px) at t = 0 (quiet) → t = 1 (loud), measured from the 티맵 voice screen:
// the whole ring grows and thickens while the core shrinks, opening a white gap.
const OUTER = { min: 128, max: 140 }; // quiet size = the former round record button (128px)
const HOLE = { min: 92, max: 99 };
const CORE = { min: 57, max: 78 }; // core is largest when quiet

const lerp = (a: number, b: number, t: number) => a + (b - a) * t;

/**
 * Recording indicator (DESIGN.md 녹음, 티맵 음성 입력 방식): gradient ring + gradient core.
 * Louder = ring grows and thickens, core shrinks. Holds still for users who ask for reduced motion.
 */
export function VoiceOrb({ level }: VoiceOrbProps) {
  const live = level != null;
  const t = live ? Math.max(0, Math.min(1, level)) : 0;
  const size = (max: number, d: number) => `scale(${d / max})`;
  const breathe = live ? "" : "motion-safe:animate-[var(--orb-anim)]";
  const transition = live ? "transform 90ms linear" : undefined;

  return (
    <span aria-hidden className="relative flex items-center justify-center" style={{ width: OUTER.max, height: OUTER.max }}>
      <span
        className={`absolute rounded-full ${breathe}`}
        style={{
          width: OUTER.max, height: OUTER.max, background: VOICE_GRADIENT, transition,
          transform: size(OUTER.max, lerp(OUTER.min, OUTER.max, t)),
          ["--orb-anim" as string]: "orb-outer 1.6s ease-in-out infinite alternate",
        }}
      />
      <span
        className={`absolute rounded-full bg-surface ${breathe}`}
        style={{
          width: HOLE.max, height: HOLE.max, transition,
          transform: size(HOLE.max, lerp(HOLE.min, HOLE.max, t)),
          ["--orb-anim" as string]: "orb-hole 1.6s ease-in-out infinite alternate",
        }}
      />
      <span
        className={`absolute rounded-full ${breathe}`}
        style={{
          width: CORE.max, height: CORE.max, background: VOICE_GRADIENT, transition,
          transform: size(CORE.max, lerp(CORE.max, CORE.min, t)),
          ["--orb-anim" as string]: "orb-core 1.6s ease-in-out infinite alternate",
        }}
      />
    </span>
  );
}
