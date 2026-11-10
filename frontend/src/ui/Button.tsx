import type { ButtonHTMLAttributes, ReactNode } from "react";
import { DotsSpinner } from "./DotsSpinner";

type Variant = "primary" | "secondary" | "neutral" | "dark" | "small" | "small-danger" | "text" | "danger-text";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  /** DESIGN.md §4: primary (one per screen) · secondary · small pill (gray / danger) · text link · destructive text. */
  variant?: Variant;
  /** Spinner + blocks repeated taps while a request is in flight. */
  loading?: boolean;
  /** Label shown next to the spinner while loading, e.g. "확정하는 중…". */
  loadingLabel?: string;
  children: ReactNode;
}

const BASE = "inline-flex items-center justify-center gap-space-2 select-none transition-colors duration-150";

const VARIANTS: Record<Variant, string> = {
  primary:
    "h-14 w-full rounded-xl bg-brand px-4 text-button text-white active:bg-brand-pressed " +
    "disabled:bg-line disabled:text-text-weak",
  secondary:
    "h-14 w-full rounded-xl bg-brand-soft px-3 text-button text-brand active:brightness-95 " +
    "disabled:bg-line disabled:text-text-weak",
  neutral: "h-14 w-full rounded-xl border-0 bg-action-neutral px-2 text-button text-text active:brightness-95 disabled:bg-line disabled:text-text-weak",
  dark: "h-14 w-full rounded-xl border-0 bg-action-dark px-2 text-button text-white active:brightness-95 disabled:bg-line disabled:text-text-weak",
  // Small pill for side actions that must still look tappable (다른 내용도 고치기, 이 후보 버리기, 글자로 입력하기)
  small: "h-12 rounded-full bg-bg px-page text-caption font-semibold text-text-strong active:bg-line",
  "small-danger": "h-12 rounded-full bg-danger-soft px-page text-caption font-semibold text-danger active:brightness-95",
  text: "min-h-12 px-2 text-caption font-medium text-brand underline-offset-4 hover:underline",
  "danger-text": "min-h-12 px-2 text-caption font-medium text-danger underline-offset-4 hover:underline",
};

/** Shared button per DESIGN.md §4. Disabled = gray surface (not just opacity). */
export function Button({ variant = "primary", loading = false, loadingLabel, disabled, className = "", children, ...rest }: ButtonProps) {
  return (
    <button
      type="button"
      {...rest}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      className={`${BASE} ${VARIANTS[variant]} ${className}`}
    >
      {loading ? (
        <>
          <DotsSpinner size="sm" />
          {loadingLabel ?? children}
        </>
      ) : (
        children
      )}
    </button>
  );
}
