import { AppHeader } from "./AppHeader";
import { MyPageButton } from "./MyPageButton";
import { ChevronLeft, ChevronRight, Plus } from "lucide-react";
import { MicIcon } from "./MicIcon";
import { AmountText } from "./AmountText";
import { Button } from "./Button";
import { SectionGap } from "./SectionGap";
import { Skeleton, SkeletonRows } from "./Skeleton";
import { won, type CounterpartyBalanceView, type DayMarks, type LedgerEntryView } from "./types";
import { elapsedLabel } from "../utils/creditMessage";

interface LedgerViewProps {
  year: number;
  /** 1–12 */
  month: number;
  selectedDay: number;
  /** Today's day number when the shown month is the current month, else null. */
  today: number | null;
  /** day number → dots */
  marks: Record<number, DayMarks>;
  /** Entries of the selected day. */
  entries: LedgerEntryView[];
  totalUnpaid: number;
  balances: CounterpartyBalanceView[];
  onMonth: (offset: -1 | 1) => void;
  onDay: (day: number) => void;
  onOpenCounterparty: (name: string) => void;
  onPayment: () => void;
  onRecord: () => void;
  /** First load: grey bars instead of numbers and lists (DESIGN.md 5-8). */
  loading?: boolean;
  /** 마이페이지 (header person icon, same as 기록 · 세금계산서). */
  onOpenMyPage?: () => void;
}

const WEEK = ["일", "월", "화", "수", "목", "금", "토"];

/**
 * 장부 tab (DESIGN.md 장부): 받을 돈 card (the one shadow card) → calendar with dots only →
 * that day's entries → 거래처별 받을 돈. [입금 등록] sits inside the 받을 돈 card. Colours: 주황 = 받을 돈, 초록 = 결제 완료 only.
 */
export function LedgerView(props: LedgerViewProps) {
  const { year, month, selectedDay, today, marks } = props;
  const owed = props.balances.filter((row) => row.unpaid > 0);
  const ages = owed.flatMap((row) => (row.oldestDays != null ? [row.oldestDays] : []));
  const oldest = ages.length > 0 && ages.length === owed.length ? Math.max(...ages) : null;
  const firstWeekday = new Date(year, month - 1, 1).getDay();
  const daysInMonth = new Date(year, month, 0).getDate();
  const cells: (number | null)[] = [...Array(firstWeekday).fill(null), ...Array.from({ length: daysInMonth }, (_, i) => i + 1)];
  const weekday = WEEK[new Date(year, month - 1, selectedDay).getDay()];

  return (
    <div className="eb bg-surface pb-space-6">
      {/* Tab header (DESIGN.md 상단 헤더): sticky, same height as the 기록(홈) header */}
      <AppHeader action={props.onOpenMyPage && <MyPageButton onClick={props.onOpenMyPage} />}><h1 className="m-0 flex h-12 items-center text-subtitle font-bold text-text-strong">장부</h1></AppHeader>

      <section className="px-page pt-space-3 pb-space-7" aria-label="받을 돈">
        <div className="rounded-3xl bg-surface p-5 shadow-card">
          <p className="text-body font-semibold text-text-sub">받을 돈</p>
          {props.loading ? (
            <>
              <Skeleton className="mt-space-2 h-11 w-52" />
              <Skeleton className="mt-space-2 h-4 w-60" />
            </>
          ) : props.totalUnpaid > 0 ? (
            <>
              <p className="mt-space-1 text-display tabular-nums text-unpaid"><AmountText value={props.totalUnpaid} /></p>
              <p className="mt-space-1 text-caption text-text-sub">
                {owed.length}곳{oldest != null && oldest > 0 && <> · <b className="font-semibold text-text-strong">최장 {oldest}일</b></>} · 입금은 오래된 외상부터 갚은 걸로 계산해요
              </p>
              {/* 입금 lives with the money it reduces (10/9 PM) — no floating button over the calendar */}
              <Button variant="secondary" className="mt-space-4" onClick={props.onPayment}>
                <Plus className="h-5 w-5" aria-hidden /> 입금 등록
              </Button>
            </>
          ) : (
            <p className="mt-space-1 text-subtitle text-text-strong">받을 돈이 없어요</p>
          )}
        </div>
      </section>

      <SectionGap />
      <section className="px-page pt-space-5 pb-space-6" aria-label="달력">
        <div className="flex items-center justify-between">
          <button onClick={() => props.onMonth(-1)} aria-label="이전 달" className="flex h-12 w-12 items-center justify-center rounded-full text-text-sub active:bg-bg">
            <ChevronLeft className="h-6 w-6" />
          </button>
          <h2 className="text-subtitle text-text-strong">
            {year}년 {month}월
          </h2>
          <button onClick={() => props.onMonth(1)} aria-label="다음 달" className="flex h-12 w-12 items-center justify-center rounded-full text-text-sub active:bg-bg">
            <ChevronRight className="h-6 w-6" />
          </button>
        </div>

        <div className="mt-space-2 grid grid-cols-7 text-center" role="grid" aria-label={`${year}년 ${month}월`}>
          {WEEK.map((w) => (
            <span key={w} className="py-space-2 text-caption text-text-weak" role="columnheader">
              {w}
            </span>
          ))}
          {cells.map((day, index) => {
            if (day == null) return <span key={`blank-${index}`} aria-hidden />;
            const mark = marks[day] ?? {};
            const selected = day === selectedDay;
            const label = `${month}월 ${day}일${mark.unpaid ? ", 받을 돈 있음" : ""}${mark.paid ? ", 결제 완료 있음" : ""}`;
            return (
              <button
                key={day}
                role="gridcell"
                aria-selected={selected}
                aria-label={label}
                onClick={() => props.onDay(day)}
                className="flex h-12 flex-col items-center justify-center"
              >
                <span
                  className={`flex h-9 w-9 items-center justify-center rounded-full text-body tabular-nums ${
                    selected ? "bg-brand font-semibold text-white" : day === today ? "font-semibold text-text-strong ring-1 ring-text-weak" : "text-text"
                  }`}
                >
                  {day}
                </span>
                <span className="mt-0.5 flex h-1.5 gap-0.5" aria-hidden>
                  {mark.unpaid && <span className="h-1.5 w-1.5 rounded-full bg-unpaid" />}
                  {mark.paid && <span className="h-1.5 w-1.5 rounded-full bg-paid" />}
                </span>
              </button>
            );
          })}
        </div>

        <h3 className="mt-space-6 text-body font-semibold text-text-strong">
          {month}월 {selectedDay}일 ({weekday})
          {!props.loading && props.entries.length > 0 && <span className="ml-1 font-normal text-text-sub">· {props.entries.length}건</span>}
        </h3>
        {props.loading ? (
          <SkeletonRows rows={2} />
        ) : props.entries.length === 0 ? (
          <div className="py-space-6 text-center">
            <p className="text-body text-text-sub">이 날은 기록이 없어요</p>
            <Button variant="small" className="mt-space-3" onClick={props.onRecord}>
              <MicIcon className="h-4 w-4" /> 말로 기록하기
            </Button>
          </div>
        ) : (
          <ul className="m-0 mt-space-1 list-none p-0">
            {props.entries.map((entry) => (
              <li key={entry.id} className="border-b border-line last:border-b-0">
                <button onClick={() => props.onOpenCounterparty(entry.counterparty)} className="flex w-full items-center justify-between gap-space-3 py-space-4 text-left active:bg-bg">
                  <span className="min-w-0">
                    <span className="block truncate text-body font-semibold text-text-strong">{entry.counterparty}</span>
                    <span className="mt-0.5 block truncate text-caption text-text-sub">{entry.summary}</span>
                  </span>
                  <span className="shrink-0 text-right">
                    <span className="block text-body font-semibold tabular-nums text-text-strong">{won(entry.amount)}</span>
                    <StatusLabel credit={entry.credit} />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      <SectionGap />
      <section className="px-page pt-space-6 pb-space-8" aria-label="거래처별 받을 돈">
        <h2 className="text-subtitle text-text-strong">거래처별 받을 돈</h2>
        {props.loading ? (
          <SkeletonRows rows={3} />
        ) : owed.length === 0 ? (
          <p className="py-space-6 text-center text-body text-text-sub">남은 받을 돈이 없어요</p>
        ) : (
          <ul className="m-0 mt-space-1 list-none p-0">
            {owed.map((row) => (
              <li key={row.counterparty} className="border-b border-line last:border-b-0">
                <button onClick={() => props.onOpenCounterparty(row.counterparty)} className="flex min-h-14 w-full items-center justify-between gap-space-3 py-space-3 text-left active:bg-bg">
                  <span className="min-w-0">
                    <span className="block truncate text-body text-text-strong">{row.counterparty}</span>
                    {row.oldestDays != null && (
                      // No new colour for age (DESIGN.md §1): 30+ days only gets heavier, darker text
                      <span className={`mt-0.5 block text-caption ${row.oldestDays >= 30 ? "font-semibold text-text-strong" : "text-text-sub"}`}>
                        {elapsedLabel(row.oldestDays)}{row.owedCount ? ` · 외상 ${row.owedCount}건` : ""}
                      </span>
                    )}
                  </span>
                  <span className="flex shrink-0 items-center gap-space-1 text-body font-semibold tabular-nums text-unpaid-text">
                    {won(row.unpaid)} <ChevronRight className="h-5 w-5 text-text-weak" aria-hidden />
                  </span>
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

    </div>
  );
}

function StatusLabel({ credit }: { credit: boolean }) {
  return (
    <span className={`mt-0.5 flex items-center justify-end gap-space-1 text-caption ${credit ? "text-unpaid-text" : "text-paid"}`}>
      <span aria-hidden className={`h-2 w-2 rounded-full ${credit ? "bg-unpaid" : "bg-paid"}`} />
      {credit ? "미수금" : "결제 완료"}
    </span>
  );
}
