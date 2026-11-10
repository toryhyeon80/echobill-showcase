import { useEffect, useRef, useState } from "react";
import { ChevronLeft, Copy } from "lucide-react";
import { BottomSheet } from "./BottomSheet";
import { AmountText } from "./AmountText";
import { Button } from "./Button";
import { won, type SettlementView } from "./types";
import { buildCreditMessage, elapsedLabel } from "../utils/creditMessage";

interface SettlementSheetProps {
  detail: SettlementView | null;
  onClose: () => void;
  /** Opens the 입금 sheet with this counterparty chosen. */
  onPayment: (counterparty: string) => void;
  /** Owner's shop name for the message signature (마이페이지 사업자 정보); omitted → no signature line. */
  shopName?: string | null;
  /** Called after the 외상 내역 text was copied — the page shows the "복사했어요" toast. */
  onCopied?: () => void;
}

/**
 * 거래처 정산 내역 (DESIGN.md 장부): how much is still owed, which 외상 entries it comes from (oldest first),
 * and the deposits that paid them off. Explains FIFO in one line instead of showing the algorithm.
 *
 * 외상 내역 보내기 (PM 10/19): shows the exact text first, then copies it for the owner to paste into
 * KakaoTalk — the owner sees what goes out before it goes (no automatic sending in the MVP).
 */
export function SettlementSheet({ detail, onClose, onPayment, shopName, onCopied }: SettlementSheetProps) {
  const [sending, setSending] = useState(false);
  const [copyFailed, setCopyFailed] = useState(false);
  const preview = useRef<HTMLTextAreaElement>(null);
  const copyRevision = useRef(0);
  useEffect(() => { setSending(false); setCopyFailed(false); }, [detail]);
  const message = detail ? buildCreditMessage(detail, shopName) : "";

  // A copy result belongs to this exact preview, including its current text and visibility.
  useEffect(() => {
    copyRevision.current++;
    return () => { copyRevision.current++; };
  }, [detail, message, sending]);

  useEffect(() => {
    const field = preview.current;
    if (sending && field) { field.style.height = "auto"; field.style.height = `${field.scrollHeight}px`; }
  }, [message, sending]);

  async function copy() {
    const request = ++copyRevision.current;
    setCopyFailed(false);
    try {
      await navigator.clipboard.writeText(message);
    } catch {
      if (request !== copyRevision.current) return;
      setCopyFailed(true);
      // Older in-app browsers refuse the clipboard: select the text so the owner can copy it by hand
      preview.current?.focus();
      preview.current?.select();
      return;
    }
    if (request === copyRevision.current) onCopied?.();
  }

  return (
    <BottomSheet
      open={!!detail}
      title={sending ? "외상 내역 보내기" : detail?.counterparty ?? ""}
      onClose={onClose}
      footer={
        sending ? (
          <Button onClick={copy}>
            <Copy className="h-5 w-5" aria-hidden /> 글 복사하기
          </Button>
        ) : detail && detail.unpaid > 0 ? (
          <Button onClick={() => onPayment(detail.counterparty)}>입금 등록</Button>
        ) : undefined
      }
    >
      {detail && sending && (
        <>
          <Button variant="small" onClick={() => { setCopyFailed(false); setSending(false); }}>
            <ChevronLeft className="h-4 w-4" aria-hidden /> 정산 내역
          </Button>
          <p className="mt-space-4 text-caption text-text-sub">
            아래 글을 복사해서 {detail.counterparty} 사장님과의 카톡 대화방에 붙여 넣으세요.
          </p>
          <p role="status" className={copyFailed ? "mt-space-3 text-body text-text-strong" : "sr-only"}>{copyFailed ? "자동 복사가 안 돼요. 아래 선택된 글을 길게 눌러 복사해 주세요." : ""}</p>
          <textarea
            ref={preview}
            readOnly
            value={message}
            rows={message.split("\n").length}
            aria-label="보낼 글 미리 보기"
            className="mt-space-3 w-full resize-none rounded-2xl border-0 bg-bg p-4 text-body text-text-strong outline-none focus:ring-2 focus:ring-brand"
          />
          {!shopName?.trim() && <p className="mt-space-2 text-caption text-text-weak">마이페이지에서 가게 이름을 등록하면 글 끝에 함께 들어가요</p>}
        </>
      )}
      {detail && !sending && (
        <>
          <p className="text-body font-semibold text-text-sub">받을 돈</p>
          <p className={`mt-space-1 text-display tabular-nums ${detail.unpaid > 0 ? "text-unpaid" : "text-text-strong"}`}><AmountText value={detail.unpaid} /></p>
          <p className="mt-space-1 text-caption text-text-sub">
            외상 {won(detail.creditTotal)} − 입금 {won(detail.paidTotal)}
          </p>
          {detail.unpaid > 0 && detail.oldestDays != null && (
            <p className="mt-space-1 text-caption text-text-sub">
              가장 오래된 외상 <b className="font-semibold text-text-strong">{detail.oldestDaysApproximate ? "약 " : ""}{elapsedLabel(detail.oldestDays)}</b>
            </p>
          )}
          {detail.unpaid > 0 && (
            <Button variant="small" className="mt-space-4" onClick={() => setSending(true)}>
              <Copy className="h-4 w-4" aria-hidden /> 외상 내역 보내기
            </Button>
          )}

          <h3 className="mt-space-7 text-body font-semibold text-text-strong">외상 내역</h3>
          <p className="mt-space-1 text-caption text-text-weak">입금은 오래된 외상부터 갚은 걸로 계산해요</p>
          <ul className="m-0 mt-space-2 list-none p-0">
            {detail.entries.map((entry) => (
              <li key={entry.id} className="flex items-center justify-between gap-space-3 border-b border-line py-space-3 last:border-b-0">
                <span>
                  <span className="block text-body text-text-strong">{entry.dateLabel}</span>
                  <span className="mt-0.5 block text-caption text-text-sub">
                    외상 {won(entry.total)}
                    {entry.allocated > 0 && ` · 갚음 ${won(entry.allocated)}`}
                  </span>
                </span>
                <span className="shrink-0 text-right">
                  {entry.remaining > 0 ? (
                    <span className="text-body font-semibold tabular-nums text-unpaid-text">{won(entry.remaining)} 남음</span>
                  ) : (
                    <span className="flex items-center gap-space-1 text-caption text-paid">
                      <span aria-hidden className="h-2 w-2 rounded-full bg-paid" /> 다 받음
                    </span>
                  )}
                </span>
              </li>
            ))}
          </ul>

          {detail.payments.length > 0 && (
            <>
              <h3 className="mt-space-7 text-body font-semibold text-text-strong">입금 내역</h3>
              <ul className="m-0 mt-space-2 list-none p-0">
                {detail.payments.map((payment, index) => (
                  <li key={index} className="flex items-center justify-between border-b border-line py-space-3 last:border-b-0">
                    <span className="text-body text-text-strong">{payment.dateLabel}</span>
                    <span className="text-body font-semibold tabular-nums text-text-strong">{won(payment.amount)}</span>
                  </li>
                ))}
              </ul>
            </>
          )}
        </>
      )}
    </BottomSheet>
  );
}
