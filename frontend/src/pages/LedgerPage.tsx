import { isValidAmount, MAX_AMOUNT_KRW } from "../utils/amountLimits";
import { Button } from "../ui/Button";
import { useEffect, useMemo, useRef, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { currentSessionEpoch, getCounterparty, recordPayment, reviewPaymentForNewInput, retryKey, releaseRetryAttempt } from "../api/client";
import type { CounterpartyDetail } from "../types/api";
import { describeApiFailure, type ApiFailure } from "../utils/apiError";
import { useLedgerData, kstToday, ledgerNavigationDate } from "../utils/useLedgerData";
import { useAuthStore, useToastStore } from "../stores/appStore";
import { RequestError } from "../components/RequestError";

import { LedgerView } from "../ui/LedgerView";
import { SettlementSheet } from "../ui/SettlementSheet";
import { calendarMarks, counterpartyBalanceView, isDatedTrade, ledgerEntryView, settlementView } from "../utils/viewModels";
import { useBusinessName } from "../utils/useBusinessName";
import { PaymentSheet } from "../components/PaymentSheet";


/** Confirmed KST calendar, counterparty FIFO detail and deposits. */
export function LedgerPage() {
  const navigate = useNavigate();
  const location = useLocation();
  const requestedDate = ledgerNavigationDate(location.state?.date);
  const [todayYear, todayMonth, todayDay] = kstToday().split("-").map(Number);
  const [month, setMonth] = useState(() => requestedDate ? { year: Number(requestedDate.slice(0,4)), month: Number(requestedDate.slice(5,7)) } : { year: todayYear, month: todayMonth });
  const [selectedDay, setSelectedDay] = useState(() => requestedDate ? Number(requestedDate.slice(8,10)) : todayDay);
  const monthKey = `${month.year}-${String(month.month).padStart(2, "0")}`;
  const { ledger, unpaid, busy, error, userId, epoch: sessionEpoch, reload } = useLedgerData(monthKey);
  const [paymentOpen, setPaymentOpen] = useState(false);
  const [counterparty, setCounterparty] = useState("");
  const [amount, setAmount] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [uncertain, setUncertain] = useState(false);
  const [paymentPending, setPaymentPending] = useState(false);
  const [actionError, setActionError] = useState<string | null>(null);
  const [actionFailure, setActionFailure] = useState<ApiFailure | null>(null);
  const [retryAction, setRetryAction] = useState<() => void>(() => () => {});
  const [detail, setDetail] = useState<CounterpartyDetail | null>(null);
  const shopName = useBusinessName(!!detail && !!userId);
  const settlement = useMemo(() => detail ? settlementView(detail) : null, [detail]);
  const detailRevision = useRef(0);
  const paymentRevision = useRef(0);
  const [startOverOpen, setStartOverOpen] = useState(false);
  const selectedDate = `${monthKey}-${String(selectedDay).padStart(2, "0")}`;
  const entries = ledger?.entries.filter((entry) => entry.kst_date === selectedDate) ?? [];
  const balance = unpaid?.counterparties.find((row) => row.counterparty === counterparty);
  useEffect(() => {
    const confirmation = location.state?.confirmation;
    if (!confirmation || busy || error || !unpaid || !userId || confirmation.owner !== userId ||
      confirmation.epoch !== currentSessionEpoch() || typeof confirmation.counterparty !== "string") return;
    const total = unpaid.counterparties.find((row) => row.counterparty === confirmation.counterparty)?.unpaid ?? 0;
    useToastStore.getState().show(`${confirmation.counterparty} 받을 돈 총 ${total.toLocaleString("ko-KR")}원`);
    // Consume once so returning to this history entry does not repeat the confirmation.
    navigate(location.pathname, { replace: true, state: { ...location.state, confirmation: undefined } });
  }, [location.key, location.state, location.pathname, busy, error, unpaid, userId, navigate]);
  useEffect(() => {
    paymentRevision.current++;
    detailRevision.current++;
    setPaymentOpen(false); setDetail(null); setSubmitting(false); setUncertain(false); setPaymentPending(false); setStartOverOpen(false);
    setCounterparty(""); setAmount(""); setActionError(null); setActionFailure(null);
    return () => {
      detailRevision.current++; paymentRevision.current++;
      // Navigation alone cannot establish whether the server committed an uncertain payment.
    };
  }, [userId, sessionEpoch]);
  // Consume the explicit navigation action after session-reset effects and balance loading.
  useEffect(() => {
    const request = location.state?.openPayment;
    if (!request || busy || error || !unpaid || !userId) return;
    if (request.owner === userId && request.epoch === currentSessionEpoch()) openPayment("");
    navigate(location.pathname, { replace: true, state: { ...location.state, openPayment: undefined } });
  }, [location.key, location.state, location.pathname, busy, error, unpaid, userId, navigate]);

  function shiftMonth(offset: number) {
    detailRevision.current++; setDetail(null);
    const next = new Date(month.year, month.month - 1 + offset, 1);
    setMonth({ year: next.getFullYear(), month: next.getMonth() + 1 }); setSelectedDay(1);
  }
  async function submitPayment(replay = false) {
    if (submitting || !isValidAmount(amount, uncertain || replay ? Number.MAX_SAFE_INTEGER : MAX_AMOUNT_KRW) || (!uncertain && !replay && (!balance || Number(amount) > balance.unpaid))) return;
    setSubmitting(true); setActionError(null); setActionFailure(null);
    const owner = userId;
    const revision = paymentRevision.current;
    try {
      const attempt = await retryKey("payment", { counterparty, amount: Number(amount) });
      if (revision !== paymentRevision.current || useAuthStore.getState().userId !== owner) { if (!attempt.reused) releaseRetryAttempt(attempt); return; }
      if (attempt.pending) setPaymentPending(true);
      if (attempt.reused && !uncertain && !replay) {
        setUncertain(true);
        setActionError(attempt.pending ? "이전 입금이 아직 처리 중이에요. 잠시 후 같은 요청의 결과를 다시 확인해 주세요." : "이 거래처·금액의 이전 입금 결과가 아직 확인되지 않았어요. 기존 결과를 다시 확인하거나, 최신 정산 내역을 확인한 뒤 새 입금을 입력해 주세요.");
        return;
      }
      await recordPayment(counterparty, Number(amount), attempt);
      if (revision !== paymentRevision.current || useAuthStore.getState().userId !== owner) return;
      setUncertain(false); setPaymentPending(false); setPaymentOpen(false); setAmount(""); reload(); useToastStore.getState().show("입금을 등록했습니다.");
    }
    catch (failure) {
      if (revision !== paymentRevision.current || useAuthStore.getState().userId !== owner) return;
      const described = describeApiFailure(failure);
      const unknown = ["network", "server", "external", "pending", "auth", "rate"].includes(described.kind);
      setUncertain(unknown);
      setPaymentPending((previous) => unknown && (previous || described.kind === "pending"));
      setActionFailure(described); setRetryAction(() => () => void submitPayment(true));
      setActionError(unknown ? `${described.message} 거래처와 금액을 유지한 채 처리 결과를 다시 확인해 주세요.` : described.message);
    }
    finally { if (revision === paymentRevision.current && useAuthStore.getState().userId === owner) setSubmitting(false); }
  }
  async function showDetail(name: string) {
    setActionError(null); setActionFailure(null);
    const owner = userId;
    const revision = ++detailRevision.current;
    try { const result = await getCounterparty(name); if (revision === detailRevision.current && useAuthStore.getState().userId === owner) setDetail(result); }
    catch (failure) { if (revision === detailRevision.current && useAuthStore.getState().userId === owner) { const result = describeApiFailure(failure); setActionError(result.message); setActionFailure(result); setRetryAction(() => () => void showDetail(name)); } }
  }
  async function startOver() {
    if (submitting || paymentPending) return;
    setSubmitting(true); setActionError(null); setActionFailure(null);
    const owner = userId;
    const revision = paymentRevision.current;
    try {
      const current = await reviewPaymentForNewInput(counterparty, Number(amount));
      if (revision !== paymentRevision.current || useAuthStore.getState().userId !== owner) return;
      setDetail(current); setStartOverOpen(false); setPaymentOpen(false); setUncertain(false); setAmount(""); reload();
    } catch (failure) { if (revision === paymentRevision.current && useAuthStore.getState().userId === owner) { const result = describeApiFailure(failure); setActionError(result.message); setActionFailure(result); setRetryAction(() => () => void startOver()); } }
    finally { if (revision === paymentRevision.current && useAuthStore.getState().userId === owner) setSubmitting(false); }
  }
  function openPayment(name?: string) {
    detailRevision.current++;
    if (!uncertain) { setCounterparty(name ?? unpaid?.counterparties[0]?.counterparty ?? ""); setActionError(null); }
    setDetail(null); setPaymentOpen(true);
  }
  return <section>
    {!userId && !busy && <Link className="btn btn--link" to="/login">로그인하고 내 장부 보기</Link>}
    {error && <RequestError failure={error} retry={reload} />}
    {(busy || ledger && unpaid) && <LedgerView year={month.year} month={month.month} selectedDay={selectedDay}
      today={month.year === todayYear && month.month === todayMonth ? todayDay : null}
      marks={calendarMarks(ledger?.entries ?? [])} entries={entries.filter(isDatedTrade).map(ledgerEntryView)}
      totalUnpaid={unpaid?.total_unpaid ?? 0} balances={unpaid?.counterparties.map(counterpartyBalanceView) ?? []} loading={busy}
      onMonth={shiftMonth} onDay={setSelectedDay} onOpenCounterparty={(name) => void showDetail(name)}
      onPayment={() => openPayment()} onRecord={() => navigate("/record")} onOpenMyPage={() => navigate("/my-page")} />}
    {uncertain && !paymentOpen && <Button  onClick={() => setPaymentOpen(true)}>입금 처리 결과 다시 확인</Button>}
    {actionError && !paymentOpen && <p className="banner banner--error" role="alert">{actionError}</p>}
    {paymentOpen && !startOverOpen && <PaymentSheet counterparties={unpaid?.counterparties ?? []} counterparty={counterparty}
      amount={amount} busy={submitting} uncertain={uncertain} error={actionError}
      onCounterparty={setCounterparty} onAmount={setAmount} onClose={() => setPaymentOpen(false)}
      onSubmit={() => void submitPayment()} onStartOver={paymentPending ? undefined : () => setStartOverOpen(true)} />}
    {startOverOpen && <div className="dialog-backdrop"><div className="dialog" role="dialog" aria-modal="true" aria-labelledby="start-over-title">
      <h2 id="start-over-title">새 입금을 입력할까요?</h2>
      <p>이미 처리된 입금은 취소되지 않습니다. 먼저 최신 정산 내역을 확인합니다. 이후 입력하는 입금은 같은 거래처·금액이어도 별도 입금으로 등록됩니다. 내역을 확인한 뒤 새로 받은 돈만 입력해 주세요.</p>
      {actionError && <p role="alert">{actionError}</p>}
      <div className="dialog__actions"><Button  variant="secondary" disabled={submitting} onClick={() => setStartOverOpen(false)}>기존 요청 유지</Button>
        <Button  disabled={submitting} onClick={() => void startOver()}>정산 내역 확인하고 새로 입력</Button></div>
    </div></div>}
    {settlement && userId && <SettlementSheet detail={settlement} onClose={() => { detailRevision.current++; setDetail(null); }} onPayment={openPayment}
      shopName={shopName} onCopied={() => useToastStore.getState().show("복사했어요 · 카톡 대화방에 붙여 넣으세요")} />}
    {actionFailure && actionFailure.kind !== "input" && <RequestError failure={actionFailure} retry={retryAction} onDismiss={() => setActionFailure(null)} />}
  </section>;
}
