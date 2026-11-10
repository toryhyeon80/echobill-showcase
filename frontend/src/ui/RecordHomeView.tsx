import { AppHeader } from "./AppHeader";
import { useEffect, useRef, useState } from "react";
import { ChevronRight, Keyboard, Plus } from "lucide-react";
import { MyPageButton } from "./MyPageButton";
import { AmountText } from "./AmountText";
import { Button } from "./Button";
import { SectionGap } from "./SectionGap";
import { Skeleton, SkeletonRows } from "./Skeleton";
import { won } from "./types";
import { MicIcon } from "./MicIcon";

export interface RecentEntryView {
  id: string;
  counterparty: string;
  /** e.g. "배추 3박스 외 1건" */
  summary: string;
  amount: number;
  /** true = 미수금, false = 결제 완료 */
  credit: boolean;
  /** e.g. "오늘", "2일 전" */
  dateLabel: string;
}

interface RecordHomeViewProps {
  signedIn?: boolean;
  /** Only true when all-time history is known to be empty; a monthly ledger cannot establish this. */
  firstUse?: boolean;
  loadFailed?: boolean;
  /** Total still to be received; null while unknown (first use shows the empty card). */
  unpaidTotal: number | null;
  /** e.g. "7곳 · 최장 32일" (PM 10/19) */
  unpaidNote?: string;
  /** Paid-on-the-spot trades this month, for "받을 돈 없음 · 현금 거래 N건은 장부에서" (omit → no count). */
  cashCount?: number;
  /** 이미 있는 외상 옮기기 (받을 돈 옮기기). Omitted → the button is hidden until the feature ships. */
  onMoveExisting?: () => void;
  /** Proposed first-use question; omitted outside the design review until placement is approved. */
  onSkipExisting?: () => void;
  recent: RecentEntryView[];
  /** A candidate the user left before confirming (DESIGN.md §8 기록 — "이어서 확인하기"). */
  draftLabel?: string | null;
  onRecord: () => void;
  onSubmitText: (text: string) => void;
  onResumeDraft?: () => void;
  onOpenLedger: () => void;
  onOpenEntry: (id: string) => void;
  /** First load: grey bars in 받을 돈 and 최근 기록 (DESIGN.md 5-8). */
  loading?: boolean;
  /** 마이페이지 (top-right person icon). */
  onOpenMyPage?: () => void;
  /** Controlled text-input toggle (e.g. opened after a mic failure). Uncontrolled when omitted. */
  typing?: boolean;
  onTypingChange?: (typing: boolean) => void;
  text?: string;
  onTextChange?: (text: string) => void;
  busy?: boolean;
  maxLength?: number;
}

const EXAMPLE = "예: “솔바람에 배추 세 박스 사만 오천 원 외상”";

/**
 * 기록 tab first screen = the app's home. 받을 돈 is the reason owners come back (PM 10/19):
 * - 받을 돈 card right under the header ("7곳 · 최장 32일", or "받을 돈 없음" — never "0원"), then 최근 기록.
 * - The record action is a panel docked right above the bottom tab — same shape and height as the confirm
 *   screen's [내용 수정][장부 확정] panel (DESIGN.md 5-6): [⌨ 글자로] 30% · [🎤 눌러서 말하기] 70%.
 *   It never moves between states and stays in thumb reach while the list scrolls.
 * - First use (no records yet): an empty 받을 돈 card in place ("아직 없어요"), "못 받은 돈부터 적어두세요" + 이미 있는 외상 옮기기.
 * - Typing: the same docked panel turns into the text box (example as placeholder) + [🎤 말로] · [후보 만들기].
 * The bottom tab itself is rendered by the shell under this view.
 */
export function RecordHomeView(props: RecordHomeViewProps) {
  const [ownTyping, setOwnTyping] = useState(false);
  const typing = props.typing ?? ownTyping;
  const setTyping = props.onTypingChange ?? setOwnTyping;
  const [ownText, setOwnText] = useState("");
  const text = props.text ?? ownText;
  const setText = props.onTextChange ?? setOwnText;
  const input = useRef<HTMLTextAreaElement>(null);
  /** Keep keyboard accommodation local to the focused recording input. */
  const revealInput = () => {
    const field = input.current;
    if (field && document.activeElement === field) field.scrollIntoView({ block: "center" });
  };
  useEffect(() => {
    if (!typing) return;
    const viewport = window.visualViewport;
    viewport?.addEventListener("resize", revealInput);
    return () => viewport?.removeEventListener("resize", revealInput);
  }, [typing]);
  const settled = !props.loading && props.signedIn !== false && !props.loadFailed;
  const firstUse = settled && props.firstUse === true && props.recent.length === 0 && !props.unpaidTotal && !props.draftLabel;

  // 받을 돈 stays on top in every non-first-use state, typing included (PM 10/19)
  const unpaidCard = (
    <section className="px-page pt-space-3 pb-space-6" aria-label="받을 돈">
      <button onClick={props.onOpenLedger} className="block w-full rounded-3xl bg-surface p-5 text-left shadow-card">
        <span className="flex items-center justify-between text-body font-semibold text-text-sub">
          받을 돈 <ChevronRight className="h-5 w-5 text-text-weak" aria-hidden />
        </span>
        {props.loading ? (
          <>
            <Skeleton className="mt-space-2 h-11 w-52" />
            <Skeleton className="mt-space-2 h-4 w-48" />
          </>
        ) : props.unpaidTotal == null ? (
          <span className="mt-space-2 block text-body text-text-sub">{props.signedIn === false ? "로그인하면 받을 돈을 확인할 수 있어요" : props.loadFailed ? "받을 돈을 불러오지 못했어요" : "외상으로 기록하면 여기에 모여요"}</span>
        ) : props.unpaidTotal === 0 ? (
          <>
            {/* PM 10/18: never "0원". Same display size as the amount so the card keeps its height */}
            <span className="mt-space-1 block text-display text-text-strong">받을 돈 없음</span>
            <span className="mt-space-1 block text-caption text-text-weak">
              {props.cashCount === 0 ? "이번 달 현금 거래가 없어요" : props.cashCount != null ? `이번 달 현금 거래 ${props.cashCount}건은 장부에서 볼 수 있어요` : "거래 내역은 장부에서 볼 수 있어요"}
            </span>
          </>
        ) : (
          <>
            <span className="mt-space-1 block text-display tabular-nums text-unpaid"><AmountText value={props.unpaidTotal} /></span>
            {props.unpaidNote && <span className="mt-space-1 block text-caption text-text-sub">{props.unpaidNote}</span>}
          </>
        )}
      </button>
    </section>
  );

  return (
    <div className="eb flex min-h-[calc(100dvh-4rem-env(safe-area-inset-bottom))] flex-col bg-surface">
      <AppHeader action={props.onOpenMyPage && <MyPageButton onClick={props.onOpenMyPage} />}>
        <span className="text-subtitle font-bold text-text-strong">에코빌</span>
      </AppHeader>
      {props.draftLabel && (
        <div className="px-page pt-space-2">
          <button onClick={props.onResumeDraft} className="flex w-full items-center justify-between gap-space-3 rounded-2xl bg-brand-soft px-4 py-space-3 text-left">
            <span>
              <span className="block text-caption font-semibold text-brand">확인하지 않은 기록이 있어요</span>
              <span className="block text-body text-text-strong">{props.draftLabel}</span>
            </span>
            <span className="flex shrink-0 items-center text-caption font-semibold text-brand">
              이어서 확인하기 <ChevronRight className="h-5 w-5" aria-hidden />
            </span>
          </button>
        </div>
      )}

      {!firstUse && unpaidCard}
      {firstUse ? (
        <>
        {/* Empty 받을 돈 card in its real place (PM 10/19): shows where 외상 will gather. Text only —
            a grey bar here looked like the loading skeleton. Same big-text pattern as "받을 돈 없음". */}
        <section className="px-page pt-space-3 pb-space-6" aria-label="받을 돈">
          <div className="rounded-3xl bg-surface p-5 shadow-card">
            <span className="block text-body font-semibold text-text-sub">받을 돈</span>
            <span className="mt-space-1 block text-display text-text-weak">아직 없어요</span>
            {/* Third line keeps the card the same height as the other states (PM 10/19) */}
            <span className="mt-space-1 block text-caption text-text-weak">외상을 기록하면 여기에 모여요</span>
          </div>
        </section>
        {/* Same grey band and top spacing as 최근 기록 in the other states; spare space falls to the bottom */}
        <SectionGap />
        <section className="flex flex-1 flex-col items-center px-page pt-space-7 pb-space-8 text-center">
          <h1 className="text-title text-text-strong">못 받은 돈부터 적어두세요</h1>
          <p className="mt-space-2 text-body text-text-sub">지금 생각나는 외상을 말하면<br />여기 모입니다</p>
          {props.onMoveExisting && props.onSkipExisting ? (
            <section className="mt-space-6 w-full" aria-label="기존 외상 확인">
              <p className="text-body text-text-strong">예전에 적어 둔 외상이 있으세요?</p>
              <div className="mt-space-3 grid grid-cols-2 gap-space-2">
                <Button variant="dark" className="min-w-0 whitespace-nowrap max-[374px]:text-caption" disabled={props.busy} onClick={props.onMoveExisting}>있어요, 옮길게요</Button>
                <Button variant="neutral" disabled={props.busy} onClick={props.onSkipExisting}>없어요</Button>
              </div>
            </section>
          ) : props.onMoveExisting && (
            <Button variant="small" className="mt-space-6" onClick={props.onMoveExisting}>
              <Plus className="h-4 w-4" aria-hidden /> 이미 있는 외상 옮기기
            </Button>
          )}
        </section>
        </>
      ) : (
        <>
          <SectionGap />
          <section className="flex-1 px-page pt-space-7 pb-space-8" aria-label="최근 기록">
            <h2 className="mb-space-1 text-subtitle text-text-strong">최근 기록</h2>
            {props.loading ? (
              <SkeletonRows rows={3} />
            ) : props.recent.length === 0 ? (
              <p className="py-space-6 text-center text-body text-text-sub">
                {props.signedIn === false ? "로그인하면 최근 기록을 확인할 수 있어요." : props.loadFailed ? "최근 기록을 불러오지 못했어요." : "이번 달 기록이 없어요."}
                <br />
                {props.signedIn !== false && !props.loadFailed && "아래 버튼을 눌러 거래를 기록해 보세요."}
              </p>
            ) : (
              <ul className="m-0 list-none p-0">
                {props.recent.map((entry) => (
                  <li key={entry.id} className="border-b border-line last:border-b-0">
                    <button onClick={() => props.onOpenEntry(entry.id)} className="flex w-full items-center justify-between gap-space-3 py-space-4 text-left active:bg-bg">
                      <span className="min-w-0">
                        <span className="block truncate text-body font-semibold text-text-strong">{entry.counterparty}</span>
                        <span className="mt-0.5 block truncate text-caption text-text-sub">
                          {entry.dateLabel} · {entry.summary}
                        </span>
                      </span>
                      <span className="shrink-0 text-right">
                        <span className="block text-body font-semibold tabular-nums text-text-strong">{won(entry.amount)}</span>
                        <span className={`mt-0.5 flex items-center justify-end gap-space-1 text-caption ${entry.credit ? "text-unpaid-text" : "text-paid"}`}>
                          <span aria-hidden className={`h-2 w-2 rounded-full ${entry.credit ? "bg-unpaid" : "bg-paid"}`} />
                          {entry.credit ? "미수금" : "결제 완료"}
                        </span>
                      </span>
                    </button>
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}

      {/* Docked right above the bottom tab (64px + safe area) — the confirm panel's shape (ConfirmView).
          Voice and text share it: the text box grows into the same panel instead of jumping into the page. */}
      <footer
        aria-label={typing ? "글자로 입력" : "기록하기"}
        className="sticky bottom-[calc(4rem+env(safe-area-inset-bottom))] z-20 mt-auto rounded-t-3xl bg-surface px-page pt-space-4 pb-space-3 shadow-[0_-4px_24px_rgb(0_0_0/0.08)]"
      >
        {typing ? (
          <>
            <textarea
              ref={input}
              autoFocus
              onFocus={revealInput}
              value={text}
              onChange={(event) => setText(event.target.value)}
              rows={2}
              disabled={props.busy}
              maxLength={props.maxLength ?? 500}
              aria-label="거래 내용"
              placeholder={EXAMPLE}
              className="mb-space-2 block w-full resize-none rounded-xl border-0 bg-bg px-4 py-space-3 text-body text-text-strong outline-none placeholder:text-text-weak focus:ring-2 focus:ring-brand"
            />
            <div className="grid grid-cols-[minmax(104px,3fr)_minmax(0,7fr)] gap-space-2">
              <Button variant="secondary" className="min-w-0 whitespace-nowrap !gap-1 !px-2 [&_svg]:shrink-0" disabled={props.busy} onClick={() => setTyping(false)}>
                <MicIcon className="h-5 w-5" /> 말로
              </Button>
              <Button className="min-w-0 whitespace-nowrap !gap-1 !px-2 [&_svg]:shrink-0" loading={props.busy} loadingLabel="분석 중…" disabled={!text.trim() || text.length > (props.maxLength ?? 500)} onClick={() => props.onSubmitText(text.trim())}>
                후보 만들기
              </Button>
            </div>
          </>
        ) : (
          <>
            <p className="mb-space-2 truncate text-center text-caption text-text-sub">{EXAMPLE}</p>
            <div className="grid grid-cols-[minmax(104px,3fr)_minmax(0,7fr)] gap-space-2">
              <Button variant="secondary" className="min-w-0 whitespace-nowrap !gap-1 !px-2 [&_svg]:shrink-0" disabled={props.busy} onClick={() => setTyping(true)}>
                <Keyboard className="h-5 w-5" aria-hidden /> 글자로
              </Button>
              <Button className="min-w-0 whitespace-nowrap !gap-1 !px-2 [&_svg]:shrink-0" disabled={props.busy} onClick={props.onRecord}>
                <MicIcon className="h-6 w-6" /> 눌러서 말하기
              </Button>
            </div>
          </>
        )}
      </footer>
    </div>
  );
}
