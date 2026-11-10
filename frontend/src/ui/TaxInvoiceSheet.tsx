import { invoiceForm, TAX_KIND_LABEL, type TaxKind } from "../utils/taxInvoiceForm";
import { Badge } from "./Badge";
import { BottomSheet } from "./BottomSheet";
import { won, type TaxInvoiceDetail } from "./types";

interface TaxInvoiceSheetProps {
  detail: TaxInvoiceDetail | null;
  business: { name: string; numberMasked: string } | null;
  kind: TaxKind;
  onKind: (kind: TaxKind) => void;
  onClose: () => void;
}

const KINDS: TaxKind[] = ["exempt", "included", "excluded"];

/**
 * 모의 세금계산서 양식 (PM 10/24·10/26): what the mock document would look like, computed on screen only.
 * No approval number or 국세청 wording (CLAUDE.md §4-5); amounts stay black, the choice is the only pressable control.
 */
export function TaxInvoiceSheet({ detail, business, kind, onKind, onClose }: TaxInvoiceSheetProps) {
  if (!detail) return null;
  const form = invoiceForm(detail.items, detail.total, kind);
  return (
    <BottomSheet open title="모의 세금계산서 양식" onClose={onClose} tall>
      <div role="group" aria-label="과세 구분" className="grid grid-cols-3 gap-space-2">
        {KINDS.map((option) => (
          <button key={option} type="button" aria-pressed={kind === option} onClick={() => onKind(option)}
            className={`min-h-12 rounded-xl px-2 text-body ${kind === option ? "bg-brand-soft font-semibold text-brand ring-2 ring-brand" : "bg-bg text-text-sub"}`}>
            {TAX_KIND_LABEL[option]}
          </button>
        ))}
      </div>
      <p className="mt-space-2 text-caption text-text-weak">
        {kind === "exempt" ? "가공하지 않은 농산물·수산물처럼 부가세가 없는 물건은 계산서로 발행해요." : "부가세가 붙는 물건은 세금계산서로 발행해요."}
        {" "}고른 구분은 이 휴대폰에만 기억돼요.
      </p>

      <article aria-label={`모의 ${form.title}`} className="mt-space-4 rounded-2xl border border-line p-4">
        <div className="flex items-center justify-between gap-space-2">
          <h3 className="m-0 text-subtitle font-bold text-text-strong">{form.title}</h3>
          <Badge tone="status">모의 발행</Badge>
        </div>
        <p className="mt-space-1 text-caption text-text-sub">연습용 양식이에요. 실제로 국세청에 보내지 않아요.</p>

        <dl className="mt-space-4 grid grid-cols-[auto_1fr] gap-x-space-3 gap-y-space-2 text-body">
          <dt className="text-text-sub">작성일자</dt><dd className="m-0 tabular-nums text-text-strong">{detail.dateLabel}</dd>
          <dt className="text-text-sub">공급자</dt>
          <dd className="m-0 text-text-strong">{business ? <>{business.name} <span className="tabular-nums text-text-sub">{business.numberMasked}</span></> : "사업자 정보 미등록"}</dd>
          <dt className="text-text-sub">공급받는자</dt>
          <dd className="m-0 text-text-strong">{detail.counterparty} <span className="text-caption text-text-weak">(사업자번호 받지 않음)</span></dd>
        </dl>

        <ul className="m-0 mt-space-4 list-none border-t border-line p-0" aria-label="품목">
          {form.lines.map((line, index) => (
            <li key={index} className="border-b border-line py-space-3">
              <div className="flex justify-between gap-space-3 text-body text-text-strong">
                <span className="min-w-0 break-keep">{line.name || "품목"} {line.qty && <span className="text-text-sub">{line.qty}</span>}</span>
                {line.unitPrice != null && <span className="shrink-0 tabular-nums text-text-sub">단가 {won(line.unitPrice)}</span>}
              </div>
              {line.supply != null && (
                <div className="mt-space-1 flex justify-end gap-space-3 text-caption tabular-nums text-text-sub">
                  <span>공급가액 {won(line.supply)}</span><span>세액 {won(line.tax)}</span>
                </div>
              )}
            </li>
          ))}
        </ul>

        <dl className="mt-space-3 grid grid-cols-[1fr_auto] gap-y-space-1 text-body">
          <dt className="text-text-sub">공급가액</dt><dd className="m-0 text-right tabular-nums text-text-strong">{won(form.supply)}</dd>
          <dt className="text-text-sub">세액</dt><dd className="m-0 text-right tabular-nums text-text-strong">{won(form.tax)}</dd>
          <dt className="font-semibold text-text-strong">합계금액</dt>
          <dd className="m-0 text-right text-subtitle font-bold tabular-nums text-text-strong">{won(form.total)}</dd>
        </dl>
        {detail.credit !== null && (
          <p className="mt-space-2 text-right text-caption text-text-sub">이 금액을 {detail.credit ? "청구" : "영수"}함</p>
        )}
      </article>

      {kind === "excluded" && form.total !== detail.total && (
        <p className="mt-space-3 text-caption text-text-sub">
          부가세 별도면 거래처에 장부 금액({won(detail.total)})보다 부가세 {won(form.tax)}을 더 청구하는 양식이에요. 앱의 받을 돈은 장부 금액 그대로예요.
        </p>
      )}
    </BottomSheet>
  );
}
