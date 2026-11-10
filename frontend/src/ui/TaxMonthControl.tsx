import { useId } from "react";
import { ChevronDown } from "lucide-react";

/** Share the month filter's spacing and disabled behavior between the live screen and preview. */
export function TaxMonthControl({ value, disabled, onChange }: { value: string; disabled?: boolean; onChange: (month: string) => void }) {
  const id = useId();
  return <div className="flex min-w-0 flex-col gap-space-1">
    <label htmlFor={id} className="text-caption text-text-sub">거래 월</label>
    <div className="relative min-w-0">
      <input id={id} type="month" className="field-input tax-month-input pr-11 text-left" value={value} disabled={disabled}
        onChange={(event) => { if (event.target.value) onChange(event.target.value); }} />
      <ChevronDown aria-hidden className="pointer-events-none absolute right-3 top-1/2 h-5 w-5 -translate-y-1/2 text-text-sub" />
    </div>
  </div>;
}
