import { useState, type ReactNode } from "react";
import { Pencil, RotateCw } from "lucide-react";
import { MicIcon } from "./MicIcon";
import { Button } from "./Button";
import { UnderlineField } from "./UnderlineField";
import { won, type ClarifyQuestion } from "./types";

type Answer = boolean | number | string;

interface ClarifyCardProps {
  question: ClarifyQuestion;
  /** Fallback input result (tile, keypad, chip). */
  onAnswer: (value: Answer) => void;
  /** Start recording a short voice answer; the page records and calls the voice-answer API (DL-31). */
  onVoiceAnswer?: () => void;
  /** Voice answer in progress / failed message from the page. */
  voiceState?: "idle" | "listening" | "processing" | "failed";
  voiceError?: string | null;
  /** Opens the full edit sheet. */
  onEditAll: () => void;
  value?: string;
  onValueChange?: (value: string) => void;
  onReadQuestion?: () => void;
  canRead?: boolean;
  selectedCredit?: boolean | null;
  inputError?: string | null;
  hideInputConfirm?: boolean;
  /** Connected recent-name picker; previews can continue supplying static names. */
  counterpartyPicker?: ReactNode;
}

/** Read the question aloud with the browser's built-in voice (DL-31). Silently does nothing if unsupported. */
function speak(text: string) {
  if (typeof window === "undefined" || !("speechSynthesis" in window)) return;
  window.speechSynthesis.cancel();
  const utterance = new SpeechSynthesisUtterance(text);
  utterance.lang = "ko-KR";
  utterance.rate = 0.9;
  window.speechSynthesis.speak(utterance);
}

/** One missing field at a time (DESIGN.md §5-3, DL-31): question, voice answer, fallback inputs. */
export function ClarifyCard({ question, onAnswer, onVoiceAnswer, voiceState = "idle", voiceError, onEditAll, value, onValueChange, onReadQuestion, canRead = true, selectedCredit, inputError, hideInputConfirm, counterpartyPicker }: ClarifyCardProps) {
  const [ownTyped, setOwnTyped] = useState("");
  const typed = value ?? ownTyped;
  const setTyped = onValueChange ?? setOwnTyped;
  const voiceBusy = voiceState === "listening" || voiceState === "processing";
  const hasChoices = question.field === "credit" || question.field === "mismatch" && !!question.choices?.length ||
    question.field === "total" && !!question.amountHints?.length || question.field === "counterparty" && !!question.recentCounterparties?.length;

  return (
    <section className="bg-surface" aria-label="확인이 필요한 항목">
      {question.heard && <p className="mb-space-3 text-caption text-text-sub">이렇게 들렸어요: “{question.heard}”</p>}

      <div className="flex items-start justify-between gap-space-3">
        <h2 className="text-title text-text-strong">{question.question}</h2>
        {canRead && <button
          aria-label="질문 다시 듣기"
          onClick={onReadQuestion ?? (() => speak(question.question))}
          className="flex h-12 w-12 shrink-0 items-center justify-center rounded-full text-text-sub active:bg-bg"
        >
          <RotateCw className="h-6 w-6" />
        </button>}
      </div>

      {question.voiceEnabled && onVoiceAnswer && (
        <button
          onClick={onVoiceAnswer}
          disabled={voiceBusy}
          className="mt-space-4 flex h-16 w-full items-center justify-center gap-space-2 rounded-xl bg-brand-soft text-button text-brand disabled:opacity-70"
        >
          <MicIcon className={`h-6 w-6 ${voiceState === "listening" ? "animate-pulse" : ""}`} />
          {voiceState === "listening" ? "듣고 있어요… 다시 누르면 끝나요" : voiceState === "processing" ? "확인하는 중…" : "말로 대답하기"}
        </button>
      )}
      {voiceError && (
        <p role="alert" className="mt-space-3 rounded-xl bg-danger-soft px-4 py-space-3 text-caption text-danger">
          {voiceError}
        </p>
      )}

      <p className="mt-space-5 mb-space-3 text-caption text-text-sub">{question.voiceEnabled ? "또는 " : ""}{hasChoices ? "눌러서 골라 주세요" : "직접 입력해 주세요"}</p>

      {question.field === "credit" && (
        <div className="grid grid-cols-2 gap-space-3">
          <ChoiceTile selected={selectedCredit === true} label="미수금" sub="나중에 받아요" onClick={() => onAnswer(true)} />
          <ChoiceTile selected={selectedCredit === false} label="결제 완료" sub="이미 받았어요" onClick={() => onAnswer(false)} />
        </div>
      )}

      {question.field === "mismatch" && question.choices && (
        <div className="grid grid-cols-2 gap-space-3">
          {question.choices.map((amount) => (
            <ChoiceTile key={amount} selected={typed !== "" && Number(typed) === amount} label={won(amount)} onClick={() => onAnswer(amount)} />
          ))}
        </div>
      )}

      {(question.field === "qty" || question.field === "total") && (
        <div>
          {question.field === "total" && !!question.amountHints?.length && (
            <div className="mb-space-3 flex flex-wrap gap-space-2">
              {question.amountHints.map((amount) => (
                <Chip key={amount} label={won(amount)} onClick={() => onAnswer(amount)} />
              ))}
            </div>
          )}
          <UnderlineField
            label={question.field === "qty" ? `${question.itemName ?? "품목"} 수량` : "총액"}
            value={typed}
            error={inputError}
            onChange={setTyped}
            numeric={question.field === "total"}
            decimal={question.field === "qty"}
            suffix={question.field === "qty" ? question.unit ?? undefined : "원"}
          />
          <Button hidden={hideInputConfirm} className="mt-space-3" disabled={!typed} onClick={() => onAnswer(Number(typed))}>
            확인
          </Button>
        </div>
      )}

      {question.field === "counterparty" && (
        <div>
          {counterpartyPicker ?? (!!question.recentCounterparties?.length && (
            <div className="mb-space-3 flex flex-wrap gap-space-2">
              {question.recentCounterparties.map((name) => (
                <Chip key={name} label={name} onClick={() => onAnswer(name)} />
              ))}
            </div>
          ))}
          <UnderlineField label="거래처 이름" value={typed} error={inputError} onChange={setTyped} placeholder="예: 솔바람" />
          <Button hidden={hideInputConfirm} className="mt-space-3" disabled={!typed.trim()} onClick={() => onAnswer(typed.trim())}>
            확인
          </Button>
        </div>
      )}

      <div className="mt-space-4 text-center">
        <Button variant="small" onClick={onEditAll}>
          <Pencil className="h-4 w-4" aria-hidden /> 다른 내용도 고치기
        </Button>
      </div>
    </section>
  );
}

function ChoiceTile({ label, sub, onClick, selected }: { label: string; sub?: string; onClick: () => void; selected?: boolean }) {
  return (
    <button onClick={onClick} aria-pressed={selected} className={`flex min-h-24 flex-col items-center justify-center rounded-xl px-3 active:bg-brand-soft ${selected ? "bg-brand-soft ring-2 ring-brand" : "bg-bg"}`}>
      <span className="text-subtitle text-text-strong">{label}</span>
      {sub && <span className="mt-space-1 text-caption text-text-sub">{sub}</span>}
    </button>
  );
}

function Chip({ label, onClick }: { label: string; onClick: () => void }) {
  return (
    <button onClick={onClick} className="h-12 rounded-full bg-bg px-4 text-body font-medium text-text-strong active:bg-brand-soft">
      {label}
    </button>
  );
}
