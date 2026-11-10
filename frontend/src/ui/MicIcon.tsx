import { useId } from "react";

/**
 * Filled microphone as in 티맵 (the former round record button): solid capsule with a small hole,
 * holder arc, stem and flat base. Uses currentColor so it follows the button's text colour.
 */
export function MicIcon({ className = "h-5 w-5" }: { className?: string }) {
  const mask = `mic-hole-${useId().replace(/[^a-zA-Z0-9_-]/g, "")}`; // useId has ":" — unsafe inside url(#…)
  return (
    <svg viewBox="0 0 24 24" className={className} fill="currentColor" aria-hidden>
      <mask id={mask}>
        <rect width="24" height="24" fill="white" />
        <circle cx="12" cy="4.7" r="1.5" fill="black" />
      </mask>
      <rect x="7.5" y="0.6" width="9.1" height="14.9" rx="4.55" mask={`url(#${mask})`} />
      <path d="M4.6 10.8v0.2a7.4 7.4 0 0 0 14.8 0v-0.2" fill="none" stroke="currentColor" strokeWidth={1.7} />
      <rect x="11.25" y="18.2" width="1.5" height="3.6" />
      <rect x="8.6" y="21.6" width="6.8" height="1.8" />
    </svg>
  );
}
