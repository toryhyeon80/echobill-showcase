import { won, type SettlementView } from "../ui/types";

/**
 * 외상 내역 보내기 (PM 10/19): the text the owner pastes into the KakaoTalk chat with that counterparty.
 * Only entries still owed are listed (FIFO remainder from the server), oldest first; a partly paid entry
 * says how much of it is left. Polite and neutral — it is a statement, not a demand ("독촉" 아님).
 */
export function buildCreditMessage(detail: SettlementView, shopName?: string | null): string {
  const owed = detail.entries.filter((entry) => entry.remaining > 0);
  const lines = owed.map((entry) =>
    entry.allocated > 0
      ? `· ${entry.messageDateLabel ?? entry.dateLabel} ${won(entry.total)} 중 ${won(entry.remaining)} 남음`
      : `· ${entry.messageDateLabel ?? entry.dateLabel} ${won(entry.remaining)}`,
  );
  const sign = shopName?.trim() ? [`— ${shopName.trim()}`] : [];
  return [
    `${detail.counterparty} 사장님, 안녕하세요.`,
    "외상 내역 정리해서 보내드립니다.",
    "",
    ...lines,
    `합계 ${won(detail.unpaid)}`,
    "",
    "확인 부탁드립니다. 감사합니다.",
    ...sign,
  ].join("\n");
}

/** "오늘" / "N일 경과" for the oldest unpaid credit — a credit from today is not "0일 경과" (병철 #171 리뷰). */
export function elapsedLabel(days: number): string {
  return days <= 0 ? "오늘" : `${days}일 경과`;
}

/** "오늘" / "N일 전" for the home's recent list (KST calendar days, both YYYY-MM-DD). */
export function daysAgoLabel(kstDate: string, todayKst: string): string {
  const days = daysBetween(kstDate, todayKst);
  return days <= 0 ? "오늘" : `${days}일 전`;
}

/** Whole KST calendar days from `fromKst` to `todayKst` (both YYYY-MM-DD); never negative. */
export function daysBetween(fromKst: string, todayKst: string): number {
  const day = (value: string) => Date.UTC(Number(value.slice(0, 4)), Number(value.slice(5, 7)) - 1, Number(value.slice(8, 10)));
  return Math.max(0, Math.round((day(todayKst) - day(fromKst)) / 86_400_000));
}
