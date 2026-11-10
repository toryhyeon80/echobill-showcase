import { useEffect } from "react";

interface GreetingViewProps {
  /** 상호 from 사업자 정보 if registered; we never collect a personal name (개인정보 처리방침). */
  businessName?: string | null;
  /** Called after the short pause or on tap — the page goes to 기록(홈). */
  onDone: () => void;
  /** Auto-continue delay; the page can also call onDone as soon as home data is ready. */
  durationMs?: number;
}

/**
 * Returning-member greeting after Kakao sign-in (DESIGN.md 로그인 인사, 토스 방식).
 * Centered, nothing else on screen; continues by itself, tap anywhere to skip.
 */
export function GreetingView({ businessName, onDone, durationMs = 1400 }: GreetingViewProps) {
  useEffect(() => {
    const id = window.setTimeout(onDone, durationMs);
    return () => window.clearTimeout(id);
  }, [onDone, durationMs]);

  return (
    <button
      onClick={onDone}
      aria-label="기록 화면으로 넘어가기"
      className="eb flex min-h-[100dvh] w-full flex-col items-center justify-center bg-surface px-page text-center motion-safe:animate-[fadein_300ms_ease-out]"
    >
      <span className="text-body text-text-sub">로그인 완료!</span>
      <span role="status" className="mt-space-4 text-title text-text-strong">
        {/* Same greeting with or without a 상호 (10/12 PM) */}
        {businessName ? `${businessName} 사장님,` : "사장님,"}
        <br />
        환영합니다!
      </span>
    </button>
  );
}
