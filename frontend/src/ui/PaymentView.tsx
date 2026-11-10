import { isValidAmount, MAX_AMOUNT_KRW } from "../utils/amountLimits";
import { AppHeader } from "./AppHeader";
import { useRef, useState } from "react";
import { useModalFocus } from "./useModalFocus";
import { ChevronDown, ChevronLeft } from "lucide-react";
import { koreanAmount } from "./AmountKeypad";
import { KeypadFooter } from "./KeypadFooter";
import { BottomSheet } from "./BottomSheet";
import { AmountText } from "./AmountText";
import { Button } from "./Button";
import { won, type CounterpartyBalanceView } from "./types";

interface PaymentViewProps {
  balances: CounterpartyBalanceView[];
  counterparty: string;
  /** Plain digits. */
  amount: string;
  submitting?: boolean;
  uncertain?: boolean;
  onStartOver?: () => void;
  /** Server error (409 / network) shown above the button. */
  error?: string | null;
  onCounterparty: (name: string) => void;
  onAmount: (digits: string) => void;
  onBack: () => void;
  onSubmit: () => void;
}

/**
 * 입금 등록 — full screen, 토스 송금 방식 (DESIGN.md 입금):
 * who (one line, tap to change) → big amount + 한글 읽기 → 받을 돈 / [전액] → on-screen keypad → "N원 입금 등록".
 * Over-payment and the one-payment limit are checked before submission. Long names and guidance may grow; the screen remains scrollable.
 */
export function PaymentView(props: PaymentViewProps) {
  const ref = useRef<HTMLDivElement>(null);
  useModalFocus(true, ref);
  const [picking, setPicking] = useState(false);
  const balance = props.balances.find((row) => row.counterparty === props.counterparty)?.unpaid ?? 0;
  const value = Number(props.amount || 0);
  const locked = props.submitting || props.uncertain;
  const over = !props.uncertain && value > balance;
  const overLimit = !props.uncertain && value > MAX_AMOUNT_KRW;
  const fullOverLimit = balance > MAX_AMOUNT_KRW;
  const canSubmit = !!props.counterparty && isValidAmount(props.amount, props.uncertain ? Number.MAX_SAFE_INTEGER : MAX_AMOUNT_KRW) && !over;

  return (
    <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label="입금 등록" className="eb fixed inset-0 z-50 mx-auto flex max-w-md flex-col overflow-y-auto bg-surface">
      <AppHeader><button disabled={props.submitting} onClick={props.onBack} className="flex h-12 items-center gap-0.5 rounded-xl text-body font-medium text-text-strong">
          <ChevronLeft className="h-6 w-6" aria-hidden /> 입금 등록
        </button></AppHeader>

      <main className="flex shrink-0 flex-col items-center px-page pt-[6dvh] text-center">
        <button disabled={locked} onClick={() => setPicking(true)} className="flex min-h-12 w-full flex-col items-center justify-center gap-space-1 rounded-2xl px-3 py-space-2 text-body font-semibold text-text-strong active:bg-bg">
          <span className="block w-full">{props.counterparty || "거래처 고르기"}</span>
          <span className="flex items-center gap-space-1 text-caption font-normal text-text-sub">{props.counterparty ? "에서 받은 돈" : "거래처 선택"}<ChevronDown className="h-5 w-5 shrink-0" aria-hidden /></span>
        </button>

        <p
          aria-live="polite"
          className={`mt-space-4 w-full text-[2.5rem] leading-[3rem] font-bold tabular-nums ${value ? (over ? "text-danger" : "text-text-strong") : "text-text-weak"}`}
        >
          {value ? <AmountText value={value} /> : "받은 금액 입력"}
        </p>
        <p className="mt-space-1 h-6 text-caption text-text-sub">{koreanAmount(value)}</p>

        <div className="mt-space-3 flex h-12 items-center gap-space-2">
          {overLimit ? (
            <p role="alert" className="text-caption font-semibold text-danger">입금은 한 번에 {won(MAX_AMOUNT_KRW)}까지예요. 나누어 입력해 주세요.</p>
          ) : over ? (
            <p role="alert" className="text-caption font-semibold text-danger">
              받을 돈({won(balance)})보다 많아요
            </p>
          ) : (
            props.counterparty && (
              <>
                <span className="text-caption text-text-sub">받을 돈 {won(balance)}</span>
                <Button variant="small" className="!px-4" disabled={locked || fullOverLimit} onClick={() => props.onAmount(String(balance))}>
                  전액
                </Button>
              </>
            )
          )}
        </div>
        {fullOverLimit && !props.uncertain && !overLimit && <p role="status" className="mt-space-2 text-caption text-text-sub">받을 돈이 1회 입금 한도를 넘어요. 한 번에 {won(MAX_AMOUNT_KRW)} 이하로 나누어 입력해 주세요.</p>}
      </main>

      <KeypadFooter disabled={locked} value={props.amount} onChange={props.onAmount} error={props.error}>
        <Button disabled={!canSubmit} loading={props.submitting} loadingLabel="등록하는 중…" onClick={props.onSubmit}>
          {props.uncertain ? "처리 결과 다시 확인" : canSubmit ? `${won(value)} 입금 등록` : "입금 등록"}
        </Button>
        {props.uncertain && props.onStartOver && <Button variant="small" disabled={props.submitting} className="mt-space-2" onClick={props.onStartOver}>정산 확인하고 새로 입력</Button>}
      </KeypadFooter>

      <BottomSheet open={picking} title="누가 보냈나요?" onClose={() => setPicking(false)}>
        <ul className="m-0 list-none p-0" role="radiogroup" aria-label="거래처">
          {props.balances.map((row) => {
            const selected = row.counterparty === props.counterparty;
            return (
              <li key={row.counterparty}>
                <button
                  role="radio"
                  aria-checked={selected}
                  onClick={() => {
                    props.onCounterparty(row.counterparty);
                    setPicking(false);
                  }}
                  className={`flex min-h-16 w-full flex-col items-start justify-center gap-space-1 rounded-2xl px-4 py-space-3 text-left ${selected ? "bg-brand-soft" : "active:bg-bg"}`}
                >
                  <span className={`block w-full text-body ${selected ? "font-semibold text-brand" : "text-text-strong"}`}>{row.counterparty}</span>
                  <span className="text-caption tabular-nums text-text-sub">받을 돈 {won(row.unpaid)}</span>
                </button>
              </li>
            );
          })}
        </ul>
      </BottomSheet>
    </div>
  );
}
