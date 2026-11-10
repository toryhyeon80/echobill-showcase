import { ArrowLeft } from "lucide-react";

type Key = "1" | "2" | "3" | "4" | "5" | "6" | "7" | "8" | "9" | "clear" | "0" | "back";
const KEYS: Key[] = ["1", "2", "3", "4", "5", "6", "7", "8", "9", "clear", "0", "back"];

interface AmountKeypadProps {
  /** Plain digits, no separators. */
  value: string;
  onChange: (digits: string) => void;
  /** Longest digit string accepted (default 9 = under 10억). */
  maxDigits?: number;
  disabled?: boolean;
}

/**
 * On-screen number pad (DESIGN.md 입금 · 토스 방식): big keys, 모두 지움 = clear all, ← = erase one digit.
 * Avoids the system keyboard, which pushes layouts around in the KakaoTalk in-app browser.
 */
export function AmountKeypad({ value, onChange, maxDigits = 9, disabled }: AmountKeypadProps) {
  const press = (key: Key) => {
    if (key === "back") return onChange(value.slice(0, -1));
    if (key === "clear") return onChange("");
    const next = (value + key).replace(/^0+/, ""); // no leading zeros
    if (next.length <= maxDigits) onChange(next);
  };
  return (
    <div className="grid grid-cols-3" role="group" aria-label="금액 입력 키패드">
      {KEYS.map((key) => (
        <button
          key={key}
          disabled={disabled}
          onClick={() => press(key)}
          aria-label={key === "back" ? "한 자리 지우기" : key === "clear" ? "금액 모두 지우기" : key}
          className={`flex h-16 items-center justify-center rounded-2xl active:bg-bg ${
            key === "clear" || key === "back" ? "text-text-sub" : "text-[1.75rem] font-medium tabular-nums text-text-strong"
          }`}
        >
          {key === "back" ? <ArrowLeft className="h-7 w-7" strokeWidth={1.75} aria-hidden /> : key === "clear" ? <span className="text-caption font-medium leading-tight">모두<br />지움</span> : key}
        </button>
      ))}
    </div>
  );
}

/** "129000" → "12만 9천 원", "1250000" → "125만 원", "8500" → "8천 5백 원" (read-back line under the amount). */
export function koreanAmount(won: number): string {
  if (!won) return "";
  const eok = Math.floor(won / 100_000_000);
  const man = Math.floor((won % 100_000_000) / 10_000);
  const rest = won % 10_000;
  const small = [
    [Math.floor(rest / 1000), "천"],
    [Math.floor((rest % 1000) / 100), "백"],
    [Math.floor((rest % 100) / 10), "십"],
    [rest % 10, ""],
  ]
    .filter(([n]) => n)
    .map(([n, unit]) => `${n}${unit}`)
    .join(" ");
  const parts = [eok && `${eok.toLocaleString("ko-KR")}억`, man && `${man.toLocaleString("ko-KR")}만`, small].filter(Boolean);
  return `${parts.join(" ")} 원`;
}
