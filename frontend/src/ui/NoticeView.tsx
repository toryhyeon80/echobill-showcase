import type { ReactNode } from "react";
import { X } from "lucide-react";

interface NoticeViewProps {
  title: string;
  message: string;
  /** What we heard / what was typed (DL-29 utterance_text) so the user sees why it was refused. */
  heard?: { label: string; text: string } | null;
  /** Grey example or tip box, e.g. how to split the sentence. */
  tip?: { label: string; lines: string[] } | null;
  /** Bottom buttons (usually one 30:70 row). */
  actions: ReactNode;
  /** Top-right X → back to 기록(홈). */
  onClose?: () => void;
}

/**
 * Full-screen guidance shared by "다시 말해 주세요" cases (DL-30 · 품목 초과 · 다중 거래처) and,
 * later, error screens (network · 500 · 429 · 413). Text is anchored from the top so nothing jumps between cases.
 */
export function NoticeView({ title, message, heard, tip, actions, onClose }: NoticeViewProps) {
  return (
    <div role="dialog" aria-modal="true" aria-label={title} className="eb fixed inset-0 z-50 mx-auto flex max-w-md flex-col bg-surface px-page">
      {onClose && (
        <button
          onClick={onClose}
          aria-label="닫기"
          className="absolute top-[calc(8px+env(safe-area-inset-top))] right-2 flex h-12 w-12 items-center justify-center rounded-full text-text-sub active:bg-bg"
        >
          <X className="h-6 w-6" aria-hidden />
        </button>
      )}

      {/* Text only (10/9 PM): title at the same height as the recording screen (18dvh) */}
      <main className="flex-1 overflow-y-auto pt-[calc(18dvh+env(safe-area-inset-top))]">
        <h1 className="text-title text-text-strong" aria-live="polite">
          {title}
        </h1>
        <p className="mt-space-2 text-body text-text-sub">{message}</p>

        {heard && (
          <p className="mt-space-6 text-caption text-text-sub">
            {heard.label}: <span className="text-text">“{heard.text}”</span>
          </p>
        )}

        {tip && (
          <div className="mt-space-4 rounded-2xl bg-bg px-4 py-space-4">
            <p className="text-caption font-semibold text-text-sub">{tip.label}</p>
            <ul className="m-0 mt-space-2 list-none space-y-space-1.5 p-0">
              {tip.lines.map((line) => (
                <li key={line} className="text-body text-text-strong">
                  {line}
                </li>
              ))}
            </ul>
          </div>
        )}
      </main>

      <footer className="pt-space-3 pb-[calc(16px+env(safe-area-inset-bottom))]">{actions}</footer>
    </div>
  );
}
