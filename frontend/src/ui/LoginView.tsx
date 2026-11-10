import { AppHeader } from "./AppHeader";
import { DotsSpinner } from "./DotsSpinner";

interface LoginViewProps {
  /** Callback page: the Kakao code is being exchanged. */
  checking?: boolean;
  error?: string | null;
  /** Kakao key missing in this build — button disabled with a notice. */
  disabled?: boolean;
  onKakaoLogin: () => void;
  /** Opens the 개인정보 처리방침 sheet. */
  onOpenPrivacy: () => void;
}

/** Kakao speech-bubble symbol. Replace with the official Kakao Developers resource before release (DESIGN.md §1). */
function KakaoSymbol() {
  return (
    <svg viewBox="0 0 24 24" className="h-6 w-6" aria-hidden>
      <path fill="#000" d="M12 3C6.48 3 2 6.5 2 10.82c0 2.8 1.88 5.25 4.7 6.63l-.96 3.5c-.08.3.26.54.52.37l4.2-2.78c.5.06 1.02.1 1.54.1 5.52 0 10-3.5 10-7.82S17.52 3 12 3z" />
    </svg>
  );
}

/** Login screen (DESIGN.md §8 로그인): big centered copy, one Kakao button at the bottom, privacy link. */
export function LoginView({ checking, error, disabled, onKakaoLogin, onOpenPrivacy }: LoginViewProps) {
  return (
    <div className="eb flex min-h-[100dvh] flex-col bg-surface">
      {/* Wordmark in the header row, black (blue is only for things you can press — DESIGN.md 색 규칙) */}
      <AppHeader><span className="text-subtitle font-bold text-text-strong">에코빌</span></AppHeader>
      {/* Anchored from the top (not centered) so the copy never moves when a notice appears above the button */}
      <main className="flex-1 px-page pt-[24dvh]">
        <h1 className="text-display text-text-strong">
          말하면
          <br />
          장부가 돼요
        </h1>
        <p className="mt-space-4 text-body text-text-sub">
          거래를 말로 남기고,
          <br />
          받을 돈은 한눈에 확인하세요.
        </p>
      </main>

      <footer className="px-page pb-[calc(16px+env(safe-area-inset-bottom))]">
        {checking ? (
          <p role="status" className="flex h-14 items-center justify-center gap-space-2 text-body text-text-sub">
            <DotsSpinner size="sm" className="text-brand" /> 로그인을 확인하고 있어요…
          </p>
        ) : (
          <>
            {error && (
              <p role="alert" className="mb-space-3 rounded-xl bg-danger-soft px-4 py-space-3 text-caption text-danger">
                {error}
              </p>
            )}
            {disabled && <p className="mb-space-3 text-center text-caption text-text-sub">카카오 로그인 설정이 아직 준비되지 않았어요.</p>}
            <button
              onClick={onKakaoLogin}
              disabled={disabled}
              className="flex h-14 w-full items-center justify-center gap-space-2 rounded-xl bg-[#FEE500] text-button text-black/85 disabled:opacity-40"
            >
              <KakaoSymbol />
              카카오로 시작하기
            </button>
          </>
        )}
        <p className="mt-space-3 text-center text-caption text-text-weak">
          시작하면{" "}
          <button onClick={onOpenPrivacy} className="min-h-12 px-0.5 font-semibold text-text-sub underline underline-offset-4">
            개인정보 처리방침
          </button>
          에 따라 정보가 처리돼요
        </p>
      </footer>
    </div>
  );
}
