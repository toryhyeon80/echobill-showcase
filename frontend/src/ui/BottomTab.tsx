import type { ComponentType } from "react";
import { BookOpen, ReceiptText } from "lucide-react";
import { MicIcon } from "./MicIcon";

export type TabKey = "record" | "ledger" | "tax";

const TABS: { key: TabKey; label: string; Icon: ComponentType<{ className?: string }> }[] = [
  { key: "record", label: "기록", Icon: MicIcon },
  { key: "ledger", label: "장부", Icon: BookOpen },
  { key: "tax", label: "세금계산서", Icon: ReceiptText },
];

interface BottomTabProps {
  active: TabKey;
  onSelect: (key: TabKey) => void;
  /** Hide the tax tab until the tax screen ships (DESIGN.md §5-6). */
  showTax?: boolean;
  /** Inside another sticky dock (e.g. under the confirm CTA panel) — no sticky of its own. */
  embedded?: boolean;
}

/** Bottom navigation (DESIGN.md §5-6): 3 tabs, label ≥15px, 64px + safe area. */
export function BottomTab({ active, onSelect, showTax = true, embedded = false }: BottomTabProps) {
  const tabs = showTax ? TABS : TABS.filter((tab) => tab.key !== "tax");
  return (
    <nav className={`${embedded ? "" : "sticky bottom-0 z-30"} border-t border-line bg-surface pb-[env(safe-area-inset-bottom)]`} aria-label="주요 메뉴">
      <ul className="m-0 flex h-16 list-none p-0">
        {tabs.map(({ key, label, Icon }) => {
          const selected = key === active;
          return (
            <li key={key} className="flex-1">
              <button
                aria-current={selected ? "page" : undefined}
                onClick={() => onSelect(key)}
                className={`flex h-16 w-full flex-col items-center justify-center gap-space-1 ${selected ? "text-brand" : "text-text-weak"}`}
              >
                <Icon className="h-6 w-6" aria-hidden />
                <span className="text-caption font-semibold">{label}</span>
              </button>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}
