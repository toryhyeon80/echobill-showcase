import { useEffect, useState } from "react";
import { Check, ChevronRight } from "lucide-react";
import { BottomSheet } from "./BottomSheet";
import { Button } from "./Button";
import type { DocumentKind } from "./DocumentSheet";

interface AgreementResult {
  /** [선택] AI 개선 동의 */
  aiConsent: boolean;
}

interface TermsAgreementSheetProps {
  open: boolean;
  submitting?: boolean;
  locked?: boolean;
  error?: string | null;
  /** Closing without agreeing keeps the user un-registered (no merchant is created). */
  onClose: () => void;
  onOpenDocument: (doc: DocumentKind) => void;
  onAgree: (result: AgreementResult) => void;
}

type ItemKey = "age" | "terms" | "privacy" | "ai";
const ITEMS: { key: ItemKey; label: string; required: boolean; doc?: DocumentKind }[] = [
  { key: "age", label: "만 14세 이상이에요", required: true },
  { key: "terms", label: "이용약관 동의", required: true, doc: "terms" },
  { key: "privacy", label: "개인정보 수집·이용 및 국외 이전 안내 확인", required: true, doc: "privacy" },
  { key: "ai", label: "AI 개선에 도움 주기", required: false, doc: "ai" },
];
const NONE: Record<ItemKey, boolean> = { age: false, terms: false, privacy: false, ai: false };

/**
 * First Kakao sign-in only (DESIGN.md 가입 동의): 전체 동의 + 필수 3 + 선택 1.
 * [동의하고 시작하기] turns on only when every required item is checked.
 */
export function TermsAgreementSheet({ open, submitting, locked, error, onClose, onOpenDocument, onAgree }: TermsAgreementSheetProps) {
  const [checked, setChecked] = useState(NONE);
  useEffect(() => {
    if (open) setChecked(NONE);
  }, [open]);

  const all = ITEMS.every((item) => checked[item.key]);
  const requiredDone = ITEMS.filter((item) => item.required).every((item) => checked[item.key]);
  const setAll = (value: boolean) => setChecked({ age: value, terms: value, privacy: value, ai: value });

  return (
    <BottomSheet
      open={open}
      title="에코빌을 시작하려면 동의해 주세요"
      onClose={() => { if (!submitting) onClose(); }}
      footer={
        <Button disabled={!requiredDone} loading={submitting} loadingLabel="가입하는 중…" onClick={() => onAgree({ aiConsent: checked.ai })}>
          {locked ? "같은 요청으로 다시 확인하기" : "동의하고 시작하기"}
        </Button>
      }
    >
      {error && <p role="alert" className="mb-space-3 text-body text-danger">{error}</p>}
      <fieldset disabled={submitting || locked} className="m-0 border-0 p-0">
      <CheckRow checked={all} onToggle={() => setAll(!all)} label="전체 동의" strong />
      <div className="my-2 h-px bg-line" aria-hidden />
      <ul className="m-0 list-none p-0">
        {ITEMS.map((item) => (
          <li key={item.key}>
            <CheckRow
              checked={checked[item.key]}
              onToggle={() => setChecked((prev) => ({ ...prev, [item.key]: !prev[item.key] }))}
              label={item.label}
              tag={item.required ? "필수" : "선택"}
              onView={item.doc ? () => onOpenDocument(item.doc!) : undefined}
            />
          </li>
        ))}
      </ul>
      </fieldset>
      <p className="mt-space-3 text-caption text-text-weak">선택 항목은 동의하지 않아도 모든 기능을 쓸 수 있어요.</p>
    </BottomSheet>
  );
}

function CheckRow({ checked, onToggle, label, tag, strong, onView }: {
  checked: boolean;
  onToggle: () => void;
  label: string;
  tag?: "필수" | "선택";
  strong?: boolean;
  onView?: () => void;
}) {
  return (
    <div className="flex items-center gap-space-1">
      <button role="checkbox" aria-checked={checked} onClick={onToggle} className="flex min-h-14 flex-1 items-center gap-space-3 text-left">
        <span
          aria-hidden
          className={`flex h-7 w-7 shrink-0 items-center justify-center rounded-full transition-colors ${checked ? "bg-brand text-white" : "bg-line text-white"}`}
        >
          <Check className="h-4 w-4" strokeWidth={3} />
        </span>
        <span className={strong ? "text-subtitle text-text-strong" : "text-body text-text-strong"}>
          {tag && <span className={`mr-1.5 ${tag === "필수" ? "text-brand" : "text-text-sub"}`}>[{tag}]</span>}
          {label}
        </span>
      </button>
      {onView && (
        <button onClick={onView} aria-label={`${label} 보기`} className="flex h-12 shrink-0 items-center px-1 text-caption text-text-sub">
          보기 <ChevronRight className="h-4 w-4" aria-hidden />
        </button>
      )}
    </div>
  );
}
