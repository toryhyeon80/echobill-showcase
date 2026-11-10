import { Smartphone } from "lucide-react";

/**
 * Portrait-only guard (DESIGN.md 화면 방향, 10/12 PM). A web page inside KakaoTalk cannot lock rotation,
 * so on a phone held sideways (landscape and short, ≤ 500px tall) this covers the screen and asks to turn it back.
 * Tablets in landscape are tall enough and keep working. Pure CSS: the app underneath keeps its state.
 * Mount once at the app root.
 */
export function RotateNotice() {
  return (
    <div
      role="alert"
      className="eb fixed inset-0 z-[100] hidden flex-col items-center justify-center bg-surface px-page text-center [@media(orientation:landscape)_and_(max-height:500px)]:flex"
    >
      <Smartphone className="h-12 w-12 text-text-weak" aria-hidden />
      <p className="m-0 mt-space-4 text-title text-text-strong">휴대폰을 세로로 돌려 주세요</p>
      <p className="m-0 mt-space-2 text-body text-text-sub">쓰던 내용은 그대로 있어요</p>
    </div>
  );
}
