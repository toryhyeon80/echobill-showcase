import { AmountText } from "./AmountText";
import { AppHeader } from "./AppHeader";
import type { ReactNode } from "react";
import { ChevronLeft, Pencil, Trash2 } from "lucide-react";
import { Badge } from "./Badge";
import { Button } from "./Button";
import { SectionGap } from "./SectionGap";
import { ClarifyCard } from "./ClarifyCard";
import { won, type CandidateView, type ClarifyQuestion, type ItemView } from "./types";

interface ConfirmViewProps {
  candidate: CandidateView;
  counterpartySuggestion?: ReactNode;
  guidanceControls?: ReactNode;
  validationMessage?: string;
  /** Present when a field still has to be answered (DL-31). */
  question?: ClarifyQuestion | null;
  confirming?: boolean;
  disabled?: boolean;
  voiceState?: "idle" | "listening" | "processing" | "failed";
  voiceError?: string | null;
  onBack: () => void;
  onAnswer: (value: boolean | number | string) => void;
  onVoiceAnswer?: () => void;
  /** Switch 미수금 / 결제 완료 when the value is already known. */
  onCreditChange: (credit: boolean) => void;
  /** Open the edit sheet scrolled to this item. */
  onEditItem: (index: number) => void;
  onEdit: () => void;
  onDiscard: () => void;
  onConfirm: () => void;
  /** Bottom tab bar shown under the CTA panel (pass <BottomTab embedded />). */
  bottomTab?: ReactNode;
}

/**
 * Candidate review screen (DESIGN.md §8 확인): summary card with payment status,
 * clarification card (DL-31), item card, 내용 수정 30% / 장부 확정 70%.
 */
export function ConfirmView(props: ConfirmViewProps) {
  const { candidate, question, confirming } = props;
  const needsAnswer = !!question || candidate.credit === null;

  return (
    <div className="eb flex min-h-[100dvh] flex-col bg-surface">
      <AppHeader><button onClick={props.onBack} className="flex h-12 items-center gap-0.5 rounded-xl text-body font-medium text-text-strong">
          <ChevronLeft className="h-6 w-6" aria-hidden /> 거래 입력
        </button></AppHeader>

      <main className="flex-1">
        <div className="px-page pb-space-7">
          <Badge tone="status">내용 확인 · 장부 미확정</Badge>
          <h1 className="mt-space-3 text-title text-text-strong">이 내용이 맞나요?</h1>
          {props.guidanceControls}
          <p className="mt-space-1 text-caption text-text-sub">
            말씀하신 내용
            <br />
            {/* each comma-separated part moves to the next line as a whole ("…원, / 무 두 개 …"), 10/12 PM */}
            {candidate.utterance.split(/(?<=,)\s+/).map((part, i, parts) => (
              <span key={i}>
                {i > 0 && " "}
                <span className="inline-block">
                  {i === 0 && "“"}
                  {part}
                  {i === parts.length - 1 && "”"}
                </span>
              </span>
            ))}
          </p>

          {/* The one raised card on the screen (DESIGN.md §5 카드 그림자) */}
          <section className="mt-space-5 rounded-3xl bg-surface p-5 shadow-card" aria-label="거래 요약">
            <p className="text-subtitle text-text-strong">{candidate.counterparty ?? "거래처 확인 필요"}</p>
            {props.counterpartySuggestion}
            <p className="mt-space-1 text-display tabular-nums text-text-strong">{candidate.total == null ? won(null) : <AmountText value={candidate.total} />}</p>
            <p className="mt-space-1 text-caption text-text-sub">{candidate.dateLabel}</p>

            {/* When the clarify card is already asking 외상·현금, don't ask twice */}
            {question?.field !== "credit" && (
              <div className="mt-space-4 border-t border-line pt-space-4">
                <CreditPicker credit={candidate.credit} onChange={props.onCreditChange} />
              </div>
            )}
          </section>
        </div>

        {question && (
          <>
            <SectionGap />
            <div className="px-page py-space-7">
              <ClarifyCard
                question={question}
                onAnswer={props.onAnswer}
                onVoiceAnswer={props.onVoiceAnswer}
                voiceState={props.voiceState}
                voiceError={props.voiceError}
                onEditAll={props.onEdit}
              />
            </div>
          </>
        )}

        <SectionGap />
        <section className="px-page pt-space-7 pb-space-6" aria-label="품목">
          <h2 className="mb-space-1 text-subtitle text-text-strong">품목</h2>
          <ul className="m-0 list-none p-0">
            {candidate.items.map((item, index) => (
              <li key={index} className="border-b border-line last:border-b-0">
                <ItemRow item={item} onEdit={() => props.onEditItem(index)} />
              </li>
            ))}
          </ul>
          <p className="mt-space-2 text-caption text-text-weak">숫자가 틀렸다면 품목을 눌러 바로 고칠 수 있어요</p>

          <div className="mt-space-6 text-center">
            <Button variant="small-danger" onClick={props.onDiscard}>
              <Trash2 className="h-4 w-4" aria-hidden /> 이 후보 버리기
            </Button>
          </div>
        </section>
      </main>
      <div className="sticky bottom-0 z-30">
      <footer
        className={`rounded-t-3xl bg-surface px-page pt-space-4 shadow-[0_-4px_24px_rgb(0_0_0/0.08)] ${
          props.bottomTab ? "pb-space-3" : "pb-[calc(12px+env(safe-area-inset-bottom))]"
        }`}
      >
        {props.validationMessage ? <p role="status" className="mb-space-2 text-center text-caption text-danger">{props.validationMessage}</p> : needsAnswer && (
          <p className="mb-space-2 text-center text-caption text-text-sub">
            {question ? "위 질문에 답하면 장부에 확정할 수 있어요" : "결제 상태를 고르면 장부에 확정할 수 있어요"}
          </p>
        )}
        <div className="flex gap-space-2">
          <Button variant="secondary" className="!w-[30%]" onClick={props.onEdit}>
            내용 수정
          </Button>
          <Button className="!w-[70%]" disabled={needsAnswer || props.disabled} loading={confirming} loadingLabel="확정하는 중…" onClick={props.onConfirm}>
            장부 확정
          </Button>
        </div>
      </footer>
      {props.bottomTab}
      </div>
    </div>
  );
}

/** 결제 상태: two explicit choices (no tap-to-cycle, so one stray tap can't silently flip it). */
function CreditPicker({ credit, onChange }: { credit: boolean | null; onChange: (credit: boolean) => void }) {
  const options = [
    { value: true, label: "미수금", selected: "bg-unpaid-soft text-unpaid-text ring-2 ring-unpaid" },
    { value: false, label: "결제 완료", selected: "bg-paid-soft text-paid ring-2 ring-paid" },
  ];
  return (
    <fieldset className="m-0 border-0 p-0">
      <legend className="mb-space-2 p-0 text-caption font-semibold text-text-sub">
        결제 상태{credit === null && <span className="ml-1 text-brand">· 골라 주세요</span>}
      </legend>
      <div className="grid grid-cols-2 gap-space-2">
        {options.map((option) => {
          const isSelected = credit === option.value;
          return (
            <button
              key={option.label}
              aria-pressed={isSelected}
              onClick={() => onChange(option.value)}
              className={`h-12 rounded-xl text-button ${isSelected ? option.selected : "bg-bg text-text-sub"}`}
            >
              {option.label}
            </button>
          );
        })}
      </div>
    </fieldset>
  );
}

/** One item: name + ✏️, "수량단위 × 단가" (단가 null이면 수량만), amount on the right. Whole row opens the editor. */
function ItemRow({ item, onEdit }: { item: ItemView; onEdit: () => void }) {
  const name = item.name ?? "품목 확인 필요";
  const qty = item.qty == null ? "수량 확인 필요" : `${item.qty}${item.unit ?? ""}`;
  return (
    <button onClick={onEdit} aria-label={`${name} 고치기`} className="flex w-full items-center justify-between gap-space-3 py-space-4 text-left active:bg-bg">
      <span className="min-w-0">
        <span className="flex items-center gap-space-1.5 text-body font-semibold text-text-strong">
          <span className="truncate">{name}</span>
          <Pencil className="h-4 w-4 shrink-0 text-text-weak" aria-hidden />
        </span>
        <span className="mt-0.5 block text-caption text-text-sub">
          {qty}
          {item.unitPrice != null && ` × ${won(item.unitPrice)}`}
        </span>
      </span>
      <span className="shrink-0 text-body font-semibold tabular-nums text-text-strong">{won(item.amount)}</span>
    </button>
  );
}
