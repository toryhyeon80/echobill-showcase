import { useId } from "react";

interface UnderlineFieldProps {
  label: string;
  value: string;
  onChange: (value: string) => void;
  /** Digits only; shown with thousands separators (45,000) while the stored value stays plain digits. */
  numeric?: boolean;
  /** Quantity: digits and one decimal point ("2.5"), no separators. */
  decimal?: boolean;
  /** Digits-only field with a custom display, e.g. 사업자등록번호 "123-45-67890". Value stays plain digits. */
  digitsFormat?: { maxDigits: number; format: (digits: string) => string };
  /** Unit after the value, e.g. "원", "박스". */
  suffix?: string;
  placeholder?: string;
  /** Red underline + message (server-flagged field). */
  error?: string | null;
  /** Grey hint under the field when there is no error. */
  hint?: string;
  autoFocus?: boolean;
}

/** Group digits as text so formatting never rounds the user's input. */
const withCommas = (digits: string) => digits.replace(/\B(?=(\d{3})+(?!\d))/g, ",");

/**
 * Text field in the Toss style (DESIGN.md 입력 칸): small grey label, big value, 2px grey underline.
 * Focus → 2px brand underline, error → 2px danger underline + message. No box, no browser border.
 */
export function UnderlineField({ label, value, onChange, numeric, decimal, digitsFormat, suffix, placeholder, error, hint, autoFocus }: UnderlineFieldProps) {
  const id = useId();
  const messageId = `${id}-msg`;
  return (
    <div>
      <label htmlFor={id} className={`block text-caption ${error ? "text-danger" : "text-text-sub"}`}>
        {label}
      </label>
      <div
        className={`flex items-center border-b-2 transition-colors ${
          error ? "border-danger" : "border-line focus-within:border-brand"
        }`}
      >
        <input
          id={id}
          value={digitsFormat ? digitsFormat.format(value) : numeric ? withCommas(value) : value}
          placeholder={placeholder}
          autoFocus={autoFocus}
          inputMode={numeric || digitsFormat ? "numeric" : decimal ? "decimal" : undefined}
          aria-invalid={error ? true : undefined}
          aria-describedby={error || hint ? messageId : undefined}
          onChange={(event) => {
            const raw = event.target.value;
            if (digitsFormat) onChange(raw.replace(/[^0-9]/g, "").slice(0, digitsFormat.maxDigits));
            else if (numeric) {
              const digits = raw.replace(/,/g, "");
              if (/^\d*$/.test(digits)) onChange(digits);
            }
            else if (decimal) {
              if (/^\d*(?:\.\d*)?$/.test(raw)) onChange(raw);
            }
            else onChange(raw);
          }}
          className="h-14 min-w-0 flex-1 border-0 bg-transparent p-0 text-subtitle font-medium text-text-strong outline-none placeholder:font-normal placeholder:text-text-weak"
        />
        {suffix && <span className="ml-2 shrink-0 text-body text-text-sub">{suffix}</span>}
      </div>
      {(error || hint) && (
        <p id={messageId} className={`mt-space-2 text-caption ${error ? "text-danger" : "text-text-weak"}`}>
          {error ?? hint}
        </p>
      )}
    </div>
  );
}
