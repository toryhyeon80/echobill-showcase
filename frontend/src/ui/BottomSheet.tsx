import { useEffect, useRef, type ReactNode } from "react";
import { isTopModal, useModalFocus } from "./useModalFocus";
import { X } from "lucide-react";

interface BottomSheetProps {
  open: boolean;
  title: string;
  /** Close (X, backdrop, Escape). The caller decides whether to confirm discarding edits. */
  onClose: () => void;
  children: ReactNode;
  /** Sticky footer, usually one primary Button. */
  footer?: ReactNode;
  /** Full-height reading sheet (e.g. 개인정보 처리방침) instead of content-height. */
  tall?: boolean;
}

/** Layer over the screen (DESIGN.md §5-2): 60% backdrop, 24px top radius, one close button. */
export function BottomSheet({ open, title, onClose, children, footer, tall = false }: BottomSheetProps) {
  const ref = useRef<HTMLDivElement>(null);
  useModalFocus(open, ref);
  useEffect(() => {
    if (!open) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && isTopModal(ref.current)) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onClose]);

  if (!open) return null;
  return (
    <div ref={ref} tabIndex={-1} className="eb fixed inset-0 z-40 flex items-end justify-center" role="dialog" aria-modal="true" aria-label={title}>
      <div aria-hidden="true" className="absolute inset-0 bg-black/80" onClick={onClose} />
      <div className={`relative flex w-full max-w-md flex-col rounded-t-3xl bg-surface ${tall ? "h-[92dvh]" : "max-h-[90dvh]"}`}>
        <div className="mx-auto mt-space-3 h-1.5 w-10 rounded-full bg-line" aria-hidden />
        <div className="flex items-center justify-between px-page pt-space-3 pb-space-2">
          <h2 className="text-subtitle text-text-strong">{title}</h2>
          <button aria-label="닫기" onClick={onClose} className="flex h-12 w-12 items-center justify-center rounded-full text-text-sub">
            <X className="h-6 w-6" />
          </button>
        </div>
        <div className="flex-1 overflow-y-auto px-page pb-space-4">{children}</div>
        {footer && <div className="border-t border-line px-page pt-space-3 pb-[calc(12px+env(safe-area-inset-bottom))]">{footer}</div>}
      </div>
    </div>
  );
}
