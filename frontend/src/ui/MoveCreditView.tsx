import { useRef, useState } from "react";
import { ChevronLeft } from "lucide-react";
import { HomeIcon } from "./HomeIcon";
import { AppHeader } from "./AppHeader";
import { koreanAmount } from "./AmountKeypad";
import { KeypadFooter } from "./KeypadFooter";
import { AmountText } from "./AmountText";
import { Button } from "./Button";
import { UnderlineField } from "./UnderlineField";
import { useModalFocus } from "./useModalFocus";
import { won } from "./types";

/** 언제쯤 — rough age of the old 외상; the server turns it into a date for 경과일·차감 순서 (설계 검토 10/23). */
export type MoveWhen = "this_week" | "this_month" | "last_month" | "earlier";

const WHEN_OPTIONS: { key: MoveWhen; label: string }[] = [
  { key: "this_week", label: "이번 주" },
  { key: "this_month", label: "이번 달" },
  { key: "last_month", label: "지난달" },
  { key: "earlier", label: "그 전" },
];

export interface MoveCreditInput {
  counterparty: string;
  /** Total still owed, plain won (no items). */
  amount: number;
  when: MoveWhen;
}

interface MoveCreditViewProps {
  /** Names the owner already uses (거래처 제안과 같은 목록); empty on first use. */
  recentCounterparties?: string[];
  submitting?: boolean;
  /** Server error shown above the button. */
  error?: string | null;
  onBack: () => void;
  onSubmit: (input: MoveCreditInput) => void;
}

/**
 * 이미 있는 외상 옮기기 (받을 돈 옮기기, PM 10/18) — full screen like 입금 등록, one thing per step:
 * 1) 누구한테 받을 돈인가요? (거래처 이름) → 2) 얼마 · 언제쯤 (keypad + 4 buttons) → "N원 옮기기".
 * Only a total per counterparty, no items: it counts in 받을 돈 and FIFO, tax mock stays blocked.
 * The caller saves the input using the moved-credit API.
 */
export function MoveCreditView(props: MoveCreditViewProps) {
  const ref = useRef<HTMLDivElement>(null);
  useModalFocus(true, ref);
  const [step, setStep] = useState<"who" | "amount">("who");
  const [counterparty, setCounterparty] = useState("");
  const [amount, setAmount] = useState("");
  const [when, setWhen] = useState<MoveWhen | null>(null);
  const name = counterparty.trim();
  const value = Number(amount || 0);
  const canSubmit = !!name && value > 0 && Number.isSafeInteger(value) && !!when && !props.submitting;

  return (
    <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label="이미 있는 외상 옮기기" className="eb fixed inset-0 z-50 mx-auto flex max-w-md flex-col overflow-y-auto bg-surface">
      <AppHeader
        action={
          // DESIGN.md 홈 rule: only where ‹ takes more than one tap to get home — the amount step, not the first
          step === "amount" && (
            <button disabled={props.submitting} onClick={props.onBack} aria-label="기록 홈으로" className="flex h-12 w-12 items-center justify-center rounded-full text-text-sub active:bg-bg">
              <HomeIcon />
            </button>
          )
        }
      >
        <button disabled={props.submitting} onClick={step === "amount" ? () => setStep("who") : props.onBack} className="flex h-12 items-center gap-0.5 rounded-xl text-body font-medium text-text-strong">
          <ChevronLeft className="h-6 w-6" aria-hidden /> 이미 있는 외상 옮기기
        </button>
      </AppHeader>

      {step === "who" ? (
        <>
          <main className="px-page pt-space-6">
            <h1 className="text-title text-text-strong">누구한테 받을 돈인가요?</h1>
            <p className="mt-space-2 text-caption text-text-sub">수첩이나 다른 장부에 적어 둔 외상을 거래처마다 합계로 옮겨요.</p>
            <div className="mt-space-6">
              <UnderlineField label="거래처" value={counterparty} onChange={setCounterparty} placeholder="예: 한솥밥식당" autoFocus />
            </div>
            {!!props.recentCounterparties?.length && (
              <div className="mt-space-4 flex flex-wrap gap-space-2" aria-label="최근 거래처">
                {props.recentCounterparties.map((recent) => (
                  <Button key={recent} variant="small" onClick={() => setCounterparty(recent)}>
                    {recent}
                  </Button>
                ))}
              </div>
            )}
          </main>
          <footer className="mt-auto shrink-0 px-page pb-[calc(12px+env(safe-area-inset-bottom))]">
            <Button disabled={!name} onClick={() => setStep("amount")}>
              다음
            </Button>
          </footer>
        </>
      ) : (
        <>
          <main className="flex shrink-0 flex-col items-center px-page pt-space-4 text-center">
            <button onClick={() => setStep("who")} className="flex h-12 items-center rounded-full px-3 text-body font-semibold text-text-strong active:bg-bg">
              {name}
              <span className="ml-1 font-normal text-text-sub">받을 돈</span>
            </button>
            <p aria-live="polite" className={`mt-space-2 w-full text-[2.5rem] leading-[3rem] font-bold tabular-nums ${value ? "text-unpaid" : "text-text-weak"}`}>
              {value ? <AmountText value={value} /> : "남은 금액 입력"}
            </p>
            <p className="mt-space-1 h-6 text-caption text-text-sub">{koreanAmount(value)}</p>

            <p className="mt-space-4 text-body font-semibold text-text-strong">언제쯤 외상이에요?</p>
            <div className="mt-space-2 grid w-full grid-cols-4 gap-space-2" role="radiogroup" aria-label="언제쯤">
              {WHEN_OPTIONS.map((option) => {
                const selected = option.key === when;
                return (
                  <button
                    key={option.key}
                    role="radio"
                    aria-checked={selected}
                    onClick={() => setWhen(option.key)}
                    className={`h-12 rounded-xl border-2 text-caption font-semibold ${selected ? "border-brand bg-brand-soft text-brand" : "border-line text-text-strong active:bg-bg"}`}
                  >
                    {option.label}
                  </button>
                );
              })}
            </div>
            <p className="mt-space-2 text-caption text-text-sub">날짜와 경과일은 고른 시기를 기준으로 대략 계산해요.</p>
            <p className="mt-space-1 text-caption text-text-weak">품목 없이 남은 외상만 옮겨요 · 세금계산서는 발행할 수 없어요</p>
          </main>

          <KeypadFooter disabled={props.submitting} value={amount} onChange={setAmount} error={props.error}>
            <Button
              disabled={!canSubmit}
              loading={props.submitting}
              loadingLabel="옮기는 중…"
              onClick={() => when && props.onSubmit({ counterparty: name, amount: value, when })}
            >
              {canSubmit ? `${won(value)} 옮기기` : !when && value > 0 ? "언제쯤인지 골라 주세요" : "옮기기"}
            </Button>
          </KeypadFooter>
        </>
      )}
    </div>
  );
}
