/**
 * Plain house outline (PM 10/19): a roof whose eaves reach past the walls, and the walls — no door.
 * Drawn as two strokes so it reads as a house, not a closed home-plate shape. Uses currentColor.
 */
export function HomeIcon({ className = "h-6 w-6" }: { className?: string }) {
  return (
    <svg viewBox="0 0 24 24" className={className} fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden>
      <path d="M3 11.5 12 3.5l9 8" />
      <path d="M6 8.9V20h12V8.9" />
    </svg>
  );
}
