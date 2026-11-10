import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { currentSessionEpoch, getLedger } from "../api/client";
import { getBusinessInfo, registerBusinessInfo, getTaxInvoices, getTaxInvoice, issueTaxInvoice, type BusinessInfo, type BusinessInfoRequest, type TaxInvoice } from "../api/tax";
import { useAuthStore } from "../stores/appStore";
import type { LedgerEntry } from "../types/api";
import { describeApiFailure, type ApiFailure } from "../utils/apiError";
import { RequestError } from "../components/RequestError";
import { BusinessInfoDialog } from "../components/BusinessInfoDialog";

import { TaxInvoiceView } from "../ui/TaxInvoiceView";
import { TaxInvoiceSheet } from "../ui/TaxInvoiceSheet";
import { savedTaxKind, saveTaxKind, type TaxKind } from "../utils/taxInvoiceForm";
import { TaxMonthControl } from "../ui/TaxMonthControl";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { SkeletonRows } from "../ui/Skeleton";
import type { InvoiceRowView } from "../ui/types";
import { kstRegistrationLabel } from "../utils/useLedgerData";
import { isDatedTrade } from "../utils/viewModels";

/** Confirmed entries only; every state is explicitly a mock, with manual status recovery. */
export function TaxInvoicePage() {
  const sessionKey = useAuthStore((state) => `${state.userId}:${currentSessionEpoch()}`);
  return <TaxInvoiceSession key={sessionKey} />;
}

/** A replacement cookie session starts fresh, even when its owner ID is unchanged. */
function TaxInvoiceSession() {
  const navigate = useNavigate();
  const [issueTarget, setIssueTarget] = useState<{ entryId: string; owner: string } | null>(null);
  const userId = useAuthStore((s) => s.userId);
  const [month, setMonth] = useState(() => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit" }).format(new Date()).replace("/", "-"));
  const [entries, setEntries] = useState<LedgerEntry[]>([]);
  const [invoices, setInvoices] = useState<TaxInvoice[]>([]);
  const [business, setBusiness] = useState<BusinessInfo | null>(null);
  const [failure, setFailure] = useState<ApiFailure | null>(null);
  const [loading, setLoading] = useState(true);
  const [loaded, setLoaded] = useState(false);
  const [busy, setBusy] = useState(false);
  const [pendingEntry, setPendingEntry] = useState<string | null>(null);
  const [businessOpen, setBusinessOpen] = useState(false);
  const [businessError, setBusinessError] = useState<string | null>(null);
  const [businessFailure, setBusinessFailure] = useState<ApiFailure | null>(null);
  const [revision, setRevision] = useState(0);
  const [formEntry, setFormEntry] = useState<string | null>(null);
  const [taxKind, setTaxKind] = useState<TaxKind>(savedTaxKind);
  const epoch = useRef(0);
  useEffect(() => {
    const request = ++epoch.current;
    setLoaded(false); setIssueTarget(null); setFormEntry(null);
    setEntries([]); setInvoices([]); setBusiness(null); setFailure(null);
    setBusinessOpen(false); setBusinessError(null); setBusinessFailure(null); setPendingEntry(null); setBusy(false);
    if (!userId) { setLoading(false); return; }
    setLoading(true);
    void Promise.all([getLedger(month), getTaxInvoices(), getBusinessInfo()]).then(([ledger, list, info]) => {
      if (request !== epoch.current) return;
      // A moved credit total has no items to invoice; the server refuses it too (ENTRY_NOT_TAXABLE, PM 10/23)
      setEntries(ledger.entries.filter(isDatedTrade));
      setInvoices(list); setBusiness(info);
      setLoaded(true);
    }).catch((error: unknown) => { if (request === epoch.current) setFailure(describeApiFailure(error)); })
      .finally(() => { if (request === epoch.current) setLoading(false); });
    return () => { epoch.current++; };
  }, [userId, month, revision]);
  function applyInvoice(invoice: TaxInvoice) {
    setInvoices((list) => [invoice, ...list.filter((item) => item.entry_id !== invoice.entry_id)]);
  }
  async function issue(entryId: string) {
    if (busy || loading || (pendingEntry && pendingEntry !== entryId)) return;
    if (!business?.registered) { setBusinessError(null); setBusinessFailure(null); setBusinessOpen(true); return; }
    const request = epoch.current;
    setBusy(true); setFailure(null);
    try {
      const invoice = await issueTaxInvoice(entryId);
      if (request !== epoch.current) return;
      applyInvoice(invoice); setPendingEntry(null);
    } catch (error) {
      if (request !== epoch.current) return;
      const result = describeApiFailure(error);
      setFailure(result);
      if (["network", "server", "pending", "rate", "auth", "external"].includes(result.kind)) setPendingEntry(entryId);
      else setPendingEntry(null);
      if (result.errorCode === "BUSINESS_INFO_REQUIRED") { setBusiness({ registered: false }); setBusinessOpen(true); }
    } finally { if (request === epoch.current) setBusy(false); }
  }
  async function refresh(entryId: string) {
    if (busy) return;
    const request = epoch.current;
    setBusy(true); setFailure(null);
    try {
      const invoice = await getTaxInvoice(entryId);
      if (request === epoch.current) {
        applyInvoice(invoice);
        if (invoice.state !== "none") setPendingEntry((pending) => pending === entryId ? null : pending);
      }
    }
    catch (error) { if (request === epoch.current) setFailure(describeApiFailure(error)); }
    finally { if (request === epoch.current) setBusy(false); }
  }
  async function saveBusiness(body: BusinessInfoRequest, retryKey: string) {
    if (busy) return;
    const request = epoch.current;
    setBusy(true); setBusinessError(null); setBusinessFailure(null);
    try {
      const info = await registerBusinessInfo(body, retryKey);
      if (request !== epoch.current) return;
      setBusiness(info); setBusinessOpen(false); setFailure(null);
    } catch (error) { if (request === epoch.current) { const result = describeApiFailure(error); setBusinessError(result.message); setBusinessFailure(result); } }
    finally { if (request === epoch.current) setBusy(false); }
  }
  const rows: InvoiceRowView[] = entries.map((entry) => {
    const invoice = invoices.find((item) => item.entry_id === entry.entry_id);
    const counterparty = entry.counterparty ?? "거래처 확인 필요";
    const dateLabel = kstRegistrationLabel(entry.created_at);
    return { entryId: entry.entry_id, counterparty, amount: entry.total_amount ?? 0,
      dateLabel, status: invoice?.state ?? "none", failReason: invoice?.fail_reason,
      detail: { counterparty, dateLabel, total: entry.total_amount ?? 0, credit: entry.credit ?? null,
        items: (entry.items ?? []).map((item) => ({ name: item.name ?? null, qty: item.qty ?? null, unit: item.unit ?? null,
          unitPrice: item.unit_price ?? null, amount: item.amount ?? null })) } };
  });
  for (const invoice of invoices.filter((row) => !entries.some((entry) => entry.entry_id === row.entry_id)))
    rows.push({ entryId: invoice.entry_id, counterparty: invoice.counterparty ?? "거래처 확인 필요", amount: invoice.total_amount ?? 0,
      dateLabel: "이전 발행", status: invoice.state, failReason: invoice.fail_reason });
  const businessView = business?.registered ? { name: business.business_name ?? "내 사업자", numberMasked: business.business_number_masked ?? "등록됨" } : null;
  return <section>
    {!userId ? <p className="page"><Link className="btn" to="/login">로그인하고 확인하기</Link></p> : <>
      {failure && <RequestError failure={failure} retry={pendingEntry ? () => void issue(pendingEntry) : () => setRevision((value) => value + 1)} />}
      {pendingEntry && <p className="notice">요청 결과가 불확실합니다. 같은 요청으로 다시 확인해 주세요. 다른 거래의 발행은 확인 후 가능합니다.</p>}
      {loading ? <SkeletonRows rows={3} /> : loaded && <TaxInvoiceView
        monthControl={<TaxMonthControl value={month} disabled={busy || !!pendingEntry} onChange={setMonth} />}
        business={businessView} onOpenForm={setFormEntry}
        rows={rows} submitting={busy} pendingEntry={pendingEntry} onRefresh={(id) => void refresh(id)}
        onRegisterBusiness={() => { if (!busy && !pendingEntry) { setBusinessError(null); setBusinessFailure(null); setBusinessOpen(true); } }}
        onIssue={(id) => { if (!busy && (!pendingEntry || pendingEntry === id)) setIssueTarget({ entryId: id, owner: userId }); }} onRecord={() => navigate("/record")} onOpenMyPage={() => navigate("/my-page")} />}
    </>}
    <ConfirmDialog open={!!issueTarget && issueTarget.owner === userId} title="모의 세금계산서를 발행할까요?" message="연습용이며 실제로 국세청에 보내지 않아요."
      confirmLabel="모의 발행" onCancel={() => setIssueTarget(null)} onConfirm={() => {
        if (issueTarget && issueTarget.owner === useAuthStore.getState().userId && loaded && !busy) { const id = issueTarget.entryId; setIssueTarget(null); void issue(id); }
      }} />
    <TaxInvoiceSheet detail={rows.find((row) => row.entryId === formEntry)?.detail ?? null} business={businessView} kind={taxKind}
      onKind={(kind) => { setTaxKind(kind); saveTaxKind(kind); }} onClose={() => setFormEntry(null)} />
    {businessOpen && userId && <BusinessInfoDialog initialName={business?.business_name ?? ""} busy={busy} error={businessError} failure={businessFailure} onSave={saveBusiness} onClose={() => { if (!busy) { setBusinessOpen(false); setBusinessError(null); } }} />}
  </section>;
}
