import { Button } from "./Button";
import { useRef } from "react";
import { useModalFocus } from "./useModalFocus";

interface ConfirmDialogProps {
  open: boolean;
  title: string;
  message: string;
  confirmLabel: string;
  /** Left button label (default "취소"). */
  cancelLabel?: string;
  /** Destructive actions (e.g. 이 후보 버리기) color the confirm button red. */
  destructive?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

/** Centered confirm card (DESIGN.md §5-4): 취소 (secondary) + action. */
export function ConfirmDialog({ open, title, message, confirmLabel, cancelLabel = "취소", destructive, onConfirm, onCancel }: ConfirmDialogProps) {
  const ref = useRef<HTMLDivElement>(null);
  useModalFocus(open, ref);
  if (!open) return null;
  return (
    <div ref={ref} tabIndex={-1} className="eb fixed inset-0 z-50 flex items-center justify-center px-page" role="alertdialog" aria-modal="true" aria-label={title}>
      <div className="absolute inset-0 bg-black/80" aria-hidden />
      <div className="relative w-full max-w-sm rounded-2xl bg-surface p-6">
        <h2 className="text-subtitle text-text-strong">{title}</h2>
        <p className="mt-space-2 text-body text-text-sub">{message}</p>
        <div className="mt-space-6 flex gap-space-2">
          <Button variant="secondary" className="flex-1" onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button className={`flex-1 ${destructive ? "!bg-danger active:!bg-[#D63340]" : ""}`} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </div>
      </div>
    </div>
  );
}
