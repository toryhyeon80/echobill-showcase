import type { ReactNode } from "react";
import { AmountKeypad } from "./AmountKeypad";

interface KeypadFooterProps {
  /** Plain digits. */
  value: string;
  onChange: (digits: string) => void;
  disabled?: boolean;
  /** Server error shown above the keypad. */
  error?: string | null;
  /** The screen's button(s) under the keypad. */
  children: ReactNode;
}

/** Bottom of the amount screens (입금 등록 · 외상 옮기기): error line → on-screen keypad → main button. */
export function KeypadFooter({ value, onChange, disabled, error, children }: KeypadFooterProps) {
  return (
    <footer className="mt-auto shrink-0 px-4 pb-[calc(12px+env(safe-area-inset-bottom))]">
      {error && (
        <p role="alert" className="mx-2 mb-space-3 rounded-xl bg-danger-soft px-4 py-space-3 text-caption text-danger">
          {error}
        </p>
      )}
      <AmountKeypad disabled={disabled} value={value} onChange={onChange} />
      <div className="mt-space-3 px-2">{children}</div>
    </footer>
  );
}
