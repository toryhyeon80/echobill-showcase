import type { UnpaidList } from "../types/api";
import { PaymentView } from "../ui/PaymentView";

interface Props {
  counterparties: UnpaidList["counterparties"];
  counterparty: string;
  amount: string;
  busy: boolean;
  uncertain: boolean;
  error: string | null;
  onCounterparty: (name: string) => void;
  onAmount: (amount: string) => void;
  onClose: () => void;
  onSubmit: () => void;
  onStartOver?: () => void;
}

/** Keep uncertain payment inputs locked for replay, with an explicit return to new input. */
export function PaymentSheet(props: Props) {
  return <PaymentView balances={props.counterparties} counterparty={props.counterparty} amount={props.amount}
    submitting={props.busy} uncertain={props.uncertain} error={props.error}
    onCounterparty={props.onCounterparty} onAmount={props.onAmount} onBack={props.onClose}
    onSubmit={props.onSubmit} onStartOver={props.onStartOver} />;
}
