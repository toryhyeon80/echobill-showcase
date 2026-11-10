import { useCallback, useEffect, useState } from "react";
import { CheckCircle2 } from "lucide-react";

interface ToastMessage {
  text: string;
  /** Optional text action, e.g. "장부 보기". */
  action?: { label: string; onClick: () => void };
}

interface ToastProps {
  message: ToastMessage | null;
  onDone: () => void;
  /** Lift above a bottom tab / button panel (px). */
  offset?: number;
  durationMs?: number;
}

/**
 * 완료 알림 (DESIGN.md 토스트): dark pill near the bottom, check + one sentence, ~2.5s, no colour (검정·흰색만).
 * Announced politely to screen readers; with an action it stays a little longer.
 */
export function Toast({ message, onDone, offset = 96, durationMs = 2500 }: ToastProps) {
  useEffect(() => {
    if (!message) return;
    const id = window.setTimeout(onDone, message.action ? durationMs + 1500 : durationMs);
    return () => window.clearTimeout(id);
  }, [message, onDone, durationMs]);

  return (
    <div aria-live="polite" className="pointer-events-none fixed inset-x-0 z-[60] mx-auto flex max-w-md justify-center px-page" style={{ bottom: `calc(${offset}px + env(safe-area-inset-bottom))` }}>
      {message && (
        <div className="eb pointer-events-auto flex w-full items-center gap-space-2 rounded-2xl bg-text-strong/95 px-4 py-space-3.5 text-white shadow-lg motion-safe:animate-[fadein_200ms_ease-out]">
          <CheckCircle2 className="h-5 w-5 shrink-0" aria-hidden />
          <span className="flex-1 text-body">{message.text}</span>
          {message.action && (
            <button onClick={message.action.onClick} className="shrink-0 px-1 text-body font-semibold text-white underline underline-offset-4">
              {message.action.label}
            </button>
          )}
        </div>
      )}
    </div>
  );
}

/** Small helper: const { toast, show, clear } = useToast(); <Toast message={toast} onDone={clear} /> */
export function useToast() {
  const [toast, setToast] = useState<ToastMessage | null>(null);
  const show = useCallback((message: ToastMessage) => {
    setToast({ ...message }); // new object restarts the timer even for the same text
  }, []);
  const clear = useCallback(() => setToast(null), []);
  return { toast, show, clear };
}
