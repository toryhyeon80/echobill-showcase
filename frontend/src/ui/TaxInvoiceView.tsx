import { AppHeader } from "./AppHeader";
import { MyPageButton } from "./MyPageButton";
import type { ReactNode } from "react";
import { CheckCircle2, ChevronRight, RotateCw, Store } from "lucide-react";
import { MicIcon } from "./MicIcon";
import { Badge } from "./Badge";
import { Button } from "./Button";
import { DotsSpinner } from "./DotsSpinner";
import { SectionGap } from "./SectionGap";
import { won, type InvoiceRowView } from "./types";

interface TaxInvoiceViewProps {
  monthControl?: ReactNode;
  /** null = 사업자 정보 미등록 → 등록부터 안내. */
  business: { name: string; numberMasked: string } | null;
  rows: InvoiceRowView[];
  onRegisterBusiness: () => void;
  onIssue: (entryId: string) => void;
  onRecord: () => void;
  submitting?: boolean;
  pendingEntry?: string | null;
  onRefresh?: (entryId: string) => void;
  /** 마이페이지 (header person icon, same as 기록 · 장부). */
  onOpenMyPage?: () => void;
  /** Opens the 모의 세금계산서 양식 for a row that has its ledger entry (PM 10/26). */
  onOpenForm?: (entryId: string) => void;
}

/**
 * 세금계산서 tab (DESIGN.md 세금계산서) — 모의 발행 only: never says it was sent to 국세청.
 * 발행할 장부 (발행 전·발행 중·실패) on top, 발행한 내역 (모의 발행 완료) below.
 * No extra colour: 완료 is a grey check, 실패 is the error red, 발행 중 shows the dots.
 */
export function TaxInvoiceView({ business, rows, onRegisterBusiness, onIssue, onRecord, submitting, pendingEntry, onRefresh, monthControl, onOpenMyPage, onOpenForm }: TaxInvoiceViewProps) {
  const todo = rows.filter((row) => row.status !== "issued");
  const done = rows.filter((row) => row.status === "issued");

  return (
    <div className="eb bg-surface pb-24">
      {/* Tab header (DESIGN.md 상단 헤더): sticky, 모의 발행 badge stays visible while scrolling */}
      <AppHeader action={onOpenMyPage && <MyPageButton onClick={onOpenMyPage} />}><h1 className="m-0 flex h-12 items-center text-subtitle font-bold text-text-strong">세금계산서</h1>
        <Badge tone="status">모의 발행</Badge></AppHeader>
      {monthControl && <div className="px-page py-space-3">{monthControl}</div>}

      <section className="px-page pt-space-1 pb-space-6" aria-label="사업자 정보">
        <p className="mb-space-3 text-caption text-text-sub">연습용이에요. 실제로 국세청에 보내지 않아요.</p>
        {business ? (
          <div className="flex items-center justify-between rounded-2xl bg-bg px-4 py-space-3">
            <span className="min-w-0 flex-1">
              <span className="block text-caption text-text-sub">공급자</span>
              <span className="block text-body font-semibold text-text-strong">
                {business.name} <span className="font-normal tabular-nums text-text-sub">{business.numberMasked}</span>
              </span>
            </span>
            <Button variant="small" className="shrink-0 whitespace-nowrap bg-surface" onClick={onRegisterBusiness}>
              수정
            </Button>
          </div>
        ) : (
          <div className="rounded-3xl bg-surface p-5 shadow-card">
            <p className="text-subtitle text-text-strong">사업자 정보를 먼저 등록해 주세요</p>
            <p className="mt-space-1 text-caption text-text-sub">상호와 사업자등록번호가 있어야 모의 발행을 할 수 있어요. 한 번만 하면 돼요.</p>
            <Button className="mt-space-4" onClick={onRegisterBusiness}>
              <Store className="h-5 w-5" aria-hidden /> 사업자 정보 등록
            </Button>
          </div>
        )}
      </section>

      <SectionGap />
      <section className="px-page pt-space-6 pb-space-6" aria-label="발행할 장부">
        <h2 className="text-subtitle text-text-strong">
          발행할 장부 {todo.length > 0 && <span className="text-body font-normal text-text-sub">{todo.length}건</span>}
        </h2>
        <p className="mt-space-1 text-caption text-text-weak">장부에 확정한 거래만 발행할 수 있어요</p>
        {todo.length === 0 ? (
          <div className="py-space-6 text-center">
            <p className="text-body text-text-sub">발행할 장부가 없어요</p>
            <Button variant="small" className="mt-space-3" onClick={onRecord}>
              <MicIcon className="h-4 w-4" /> 말로 기록하기
            </Button>
          </div>
        ) : (
          <ul className="m-0 mt-space-2 list-none p-0">
            {todo.map((row) => (
              <li key={row.entryId} className="border-b border-line py-space-4 last:border-b-0">
                <div className="flex items-center justify-between gap-space-3">
                  <Summary row={row} onOpenForm={onOpenForm} />
                  <span className="shrink-0">
                    {row.status === "none" && (
                      <Button variant="small" className="shrink-0 whitespace-nowrap" disabled={!business || submitting || (!!pendingEntry && pendingEntry !== row.entryId)} onClick={() => onIssue(row.entryId)}>
                        모의 발행
                      </Button>
                    )}
                    {row.status === "requested" && (
                      <span role="status" className="flex h-10 items-center gap-space-2 text-caption text-text-sub">
                        <DotsSpinner size="sm" className="text-brand" /> 발행 중
                      </span>
                    )}
                    {row.status === "failed" && (
                      <Button variant="small" className="shrink-0 whitespace-nowrap" onClick={() => onIssue(row.entryId)}>
                        <RotateCw className="h-4 w-4" aria-hidden /> 다시 시도
                      </Button>
                    )}
                  </span>
                </div>
                {row.status === "requested" && onRefresh && <Button variant="small" disabled={submitting} onClick={() => onRefresh(row.entryId)}>상태 다시 확인</Button>}
                {row.status === "requested" && <p className="mt-space-2 text-caption text-text-weak">모의 발행은 몇 초 걸릴 수 있어요. 화면을 나가도 계속 진행돼요.</p>}
                {row.status === "failed" && (
                  <p role="alert" className="mt-space-2 text-caption text-danger">
                    발행하지 못했어요{row.failReason ? ` · ${row.failReason}` : ""}
                  </p>
                )}
              </li>
            ))}
          </ul>
        )}
      </section>

      {done.length > 0 && (
        <>
          <SectionGap />
          <section className="px-page pt-space-6 pb-space-8" aria-label="발행한 내역">
            <h2 className="text-subtitle text-text-strong">
              발행한 내역 <span className="text-body font-normal text-text-sub">{done.length}건</span>
            </h2>
            <ul className="m-0 mt-space-2 list-none p-0">
              {done.map((row) => (
                <li key={row.entryId} className="flex items-center justify-between gap-space-3 border-b border-line py-space-4 last:border-b-0">
                  <Summary row={row} onOpenForm={onOpenForm} />
                  <span className="flex shrink-0 items-center gap-space-1 text-caption text-text-sub">
                    <CheckCircle2 className="h-4 w-4" aria-hidden /> 모의 발행 완료
                  </span>
                </li>
              ))}
            </ul>
          </section>
        </>
      )}
    </div>
  );
}

function Summary({ row, onOpenForm }: { row: InvoiceRowView; onOpenForm?: (entryId: string) => void }) {
  const text = (
    <>
      <span className="block truncate text-body font-semibold text-text-strong">{row.counterparty}</span>
      <span className="mt-0.5 block text-caption tabular-nums text-text-sub">
        {row.dateLabel} · {won(row.amount)}
      </span>
    </>
  );
  // A row with its ledger entry opens the 모의 양식; older invoices without an entry stay plain text
  if (!row.detail || !onOpenForm) return <span className="min-w-0">{text}</span>;
  return (
    <button type="button" onClick={() => onOpenForm(row.entryId)} aria-label={`${row.counterparty} 모의 양식 보기`}
      className="min-w-0 rounded-lg text-left active:bg-bg">
      {text}
      <span className="mt-0.5 flex items-center text-caption text-brand">양식 보기 <ChevronRight className="h-4 w-4" aria-hidden /></span>
    </button>
  );
}
