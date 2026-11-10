import { UserRound } from "lucide-react";

/** Header person icon → 마이페이지, the same on every bottom-tab screen (기록 · 장부 · 세금계산서, PM 10/19). */
export function MyPageButton({ onClick }: { onClick: () => void }) {
  return (
    <button onClick={onClick} aria-label="마이페이지" className="flex h-12 w-12 items-center justify-center rounded-full text-text-sub active:bg-bg">
      <UserRound className="h-6 w-6" aria-hidden />
    </button>
  );
}
