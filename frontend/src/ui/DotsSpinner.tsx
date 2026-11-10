interface DotsSpinnerProps {
  /** "lg" = full-screen / section loading (brand blue), "sm" = inside buttons (current text colour). */
  size?: "sm" | "lg";
  /** Screen-reader text; omit when a visible label sits next to it. */
  label?: string;
  className?: string;
}

const SIZES = { sm: "h-5 w-5", lg: "h-12 w-12" } as const;

/**
 * EchoBill loading mark (DESIGN.md §5-8): three dots in a triangle, rotating.
 * Stops rotating for users who ask for reduced motion.
 */
export function DotsSpinner({ size = "lg", label, className = "" }: DotsSpinnerProps) {
  return (
    <span role={label ? "status" : undefined} className={`inline-flex ${className}`}>
      <svg
        viewBox="0 0 24 24"
        aria-hidden
        className={`${SIZES[size]} ${size === "lg" ? "text-brand" : ""} animate-[spin_1.2s_linear_infinite] motion-reduce:animate-none`}
      >
        <circle cx="12" cy="4.5" r="3.2" fill="currentColor" />
        <circle cx="5.5" cy="16" r="3.2" fill="currentColor" />
        <circle cx="18.5" cy="16" r="3.2" fill="currentColor" />
      </svg>
      {label && <span className="sr-only">{label}</span>}
    </span>
  );
}
