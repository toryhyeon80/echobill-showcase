import { useId } from "react";

interface ToggleProps {
  checked: boolean;
  onChange: (value: boolean) => void;
  label: string;
  description?: string;
  disabled?: boolean;
}

/** On/off switch row (DESIGN.md): label + optional grey description on the left, 52×32 switch on the right. */
export function Toggle({ checked, onChange, label, description, disabled }: ToggleProps) {
  const id = useId();
  return (
    <div className="flex items-start justify-between gap-space-4">
      <span>
        <label htmlFor={id} className="block text-body font-semibold text-text-strong">
          {label}
        </label>
        {description && <span className="mt-space-1 block text-caption text-text-sub">{description}</span>}
      </span>
      <button
        id={id}
        role="switch"
        aria-checked={checked}
        disabled={disabled}
        onClick={() => onChange(!checked)}
        className={`relative mt-0.5 h-8 w-[52px] shrink-0 rounded-full transition-colors disabled:opacity-40 ${checked ? "bg-brand" : "bg-line"}`}
      >
        <span
          aria-hidden
          className={`absolute top-1 left-1 h-6 w-6 rounded-full bg-white shadow transition-transform ${checked ? "translate-x-5" : ""}`}
        />
      </button>
    </div>
  );
}
