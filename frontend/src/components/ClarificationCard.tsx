import { useEffect, useRef, useState } from "react";
import { draftProblems, type CandidateDraft } from "../utils/candidateDraft";
import { answerClarification, applyVoiceAnswer, voiceField, clarificationQuestion, pendingClarifications, type ClarificationField } from "../utils/clarification";
import { enabledVoiceFields, submitVoiceAnswer, type VoiceAnswerField } from "../api/voiceAnswer";
import { ClarifyCard } from "../ui/ClarifyCard";
import { Button } from "../ui/Button";
import { RequestError } from "./RequestError";
import type { ApiFailure } from "../utils/apiError";
import { guidanceSummary } from "../utils/guidanceText";
import { VoiceRecorder, type VoiceRecorderHandle } from "./VoiceRecorder";
import { useAuthStore } from "../stores/appStore";
import { RecentCounterpartyPicker } from "./RecentCounterpartyPicker";
import { GuidanceSoundButton } from "./GuidanceSoundButton";
import { useGuidanceAudio } from "../utils/useGuidanceAudio";
import { prepareGuidanceAudio, readGuidance, stopGuidanceAudio } from "../utils/guidanceAudio";
import { currentSessionEpoch } from "../api/client";

interface Props {
  draft: CandidateDraft;
  busy: boolean;
  onChange: (draft: CandidateDraft) => void;
  onFinish: () => void;
  onEditAll?: () => void;
  onClose: () => void;
  transcript?: string | null;
  voiceFields?: VoiceAnswerField[];
  initialField?: ClarificationField;
  onFieldChange?: (field: ClarificationField | undefined) => void;
  amountChoices?: number[];
}

/** Ask one field at a time; explicit Next keeps typing from changing the question. */
export function ClarificationCard({ draft, busy, onChange, onFinish, onEditAll = onFinish, onClose, transcript, voiceFields, initialField, onFieldChange, amountChoices = [] }: Props) {
  const [field, setField] = useState<ClarificationField | undefined>(() => initialField ?? pendingClarifications(draft)[0]);
  const inputRef = useRef<HTMLInputElement>(null);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [answerMessage, setAnswerMessage] = useState("");
  const [failures, setFailures] = useState(0);
  const [disabledFields, setDisabledFields] = useState<VoiceAnswerField[]>([]);
  const mounted = useRef(true);
  const recorderControl = useRef<VoiceRecorderHandle>(null);
  const [voiceFailure, setVoiceFailure] = useState<ApiFailure | null>(null);
  const [firstQuestion, setFirstQuestion] = useState(true);
  const [showVoice, setShowVoice] = useState(false);
  const [amountAnswered, setAmountAnswered] = useState(false);
  const serverFields = useAuthStore((state) => state.voiceAnswerFields);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  useEffect(() => {
    onFieldChange?.(field);
    setFailures(0);
    setAnswerMessage("");
    setAmountAnswered(false);
    inputRef.current?.focus();
  }, [field]);
  const question = field === "total_amount" && amountChoices.length === 2
    ? `합계가 ${amountChoices[0].toLocaleString("ko-KR")}원이에요, ${amountChoices[1].toLocaleString("ko-KR")}원이에요?` : field ? clarificationQuestion(field, draft) : "";
  const spokenQuestion = guidanceSummary(draft, question, firstQuestion ? transcript ?? undefined : undefined, true);
  const owner = useAuthStore((state) => state.userId);
  useGuidanceAudio(spokenQuestion, `${owner}:${field}`, !!field && !busy && !voiceBusy && !showVoice);
  if (!field) return null;
  const pending = pendingClarifications(draft);
  const choosingAmount = field === "total_amount" && amountChoices.length === 2;
  const valid = !pending.includes(field) && (!choosingAmount || amountAnswered && amountChoices.includes(Number(draft.total_amount)));
  const itemIndex = Number(/\[(\d+)\]/.exec(field)?.[1]);
  const value = field === "credit" ? "" : field === "counterparty" || field === "total_amount" ? draft[field] : draft.items[itemIndex]?.qty ?? "";
  const invalid = field !== "credit" && value !== "" && pending.includes(field);
  const inputError = invalid ? draftProblems(draft).find((problem) => problem.field === field)?.message ?? "입력 내용을 확인해 주세요." : null;
  const locked = busy || voiceBusy;
  const apiField = voiceField(field);
  const canSpeak = (voiceFields ?? enabledVoiceFields(serverFields.join(","))).includes(apiField) && !disabledFields.includes(apiField) && failures < 3;

  async function answer(audio: Blob) {
    if (!field) return;
    const owner = useAuthStore.getState().userId;
    const epoch = currentSessionEpoch();
    const isCurrent = () => mounted.current && owner === useAuthStore.getState().userId && epoch === currentSessionEpoch();
    const result = await submitVoiceAnswer(voiceField(field), audio, field === "total_amount" && amountChoices.length === 2 ? amountChoices : undefined)
      .catch((error: unknown) => { if (isCurrent()) throw error; return null; });
    if (!result || !isCurrent()) return;
    setVoiceFailure(null);
    setShowVoice(false);
    const next = applyVoiceAnswer(draft, field, result);
    if (!next || field === "total_amount" && amountChoices.length === 2 && !amountChoices.includes(Number(result.value))) {
      setFailures((count) => count + 1);
      setAnswerMessage(`${result.utterance_text ? `이렇게 들렸어요: ${result.utterance_text}\n` : ""}답을 확인하기 어려워요. 이 질문에 짧게 답하거나 아래 버튼·입력칸을 사용해 주세요.`);
      return;
    }
    onChange(next);
    if (field === "total_amount") setAmountAnswered(true);
    setAnswerMessage(result.utterance_text ? `이렇게 들렸어요: ${result.utterance_text}` : "답을 채웠어요. 내용을 확인해 주세요.");
  }

  function readQuestion() {
    prepareGuidanceAudio();
    void readGuidance(spokenQuestion);
  }

  return <div className="eb" onChangeCapture={stopGuidanceAudio} onClickCapture={stopGuidanceAudio}>
    <div className="flex justify-end"><GuidanceSoundButton /></div>
    <fieldset className="design-fields" disabled={locked}>
      <ClarifyCard key={field} question={{ field: amountChoices.length === 2 ? "mismatch" : apiField, question, heard: transcript,
        itemName: draft.items[itemIndex]?.name, unit: draft.items[itemIndex]?.unit,
        voiceEnabled: canSpeak, choices: amountChoices.length === 2 ? [amountChoices[0], amountChoices[1]] : undefined }}
        counterpartyPicker={<RecentCounterpartyPicker value={draft.counterparty} onSelect={(name) => onChange(answerClarification(draft, "counterparty", name))} />}
        hideInputConfirm value={choosingAmount && !amountAnswered ? "" : value} inputError={inputError} onValueChange={(text) => onChange(answerClarification(draft, field, text))} selectedCredit={draft.credit}
        onAnswer={(answerValue) => {
          onChange(answerClarification(draft, field, typeof answerValue === "boolean" ? answerValue : String(answerValue)));
          if (field === "total_amount") setAmountAnswered(true);
        }}
        onVoiceAnswer={() => { setShowVoice(true); recorderControl.current?.start(); }} onReadQuestion={readQuestion}
        canRead
        onEditAll={onEditAll} />
    </fieldset>
    {canSpeak && <div hidden={!showVoice}><VoiceRecorder key={field} controlRef={recorderControl} disabled={busy} onAnswer={answer} onCandidate={() => {}}
      onBusy={setVoiceBusy} ensureAuth={async () => {}} onFallback={() => { setDisabledFields((fields) => [...fields, apiField]); }}
      onFailure={(failure) => {
        setVoiceFailure(failure);
        if (failure.errorCode === "VOICE_ANSWER_DISABLED" || failure.kind === "external" || failure.errorCode === "AUDIO_INVALID")
          setDisabledFields((fields) => [...fields, apiField]);
        setAnswerMessage(`${failure.utteranceText ? `이렇게 들렸어요: ${failure.utteranceText}\n` : ""}${failure.errorCode === "VOICE_ANSWER_DISABLED" ? "이 항목은 아래 버튼·입력칸으로 답해 주세요." : failure.message}`);
      }} /></div>}
    {voiceFailure && !["input", "external"].includes(voiceFailure.kind) && <RequestError failure={voiceFailure} retry={() => recorderControl.current?.retry() ?? Promise.resolve()} onDismiss={() => setVoiceFailure(null)} />}
    {answerMessage && <p className="notice whitespace-pre-line" role="status">{answerMessage}</p>}
    {field.startsWith("items[") && valid && <p className="mt-3 text-body">입력된 수량: {value}{draft.items[itemIndex]?.unit}</p>}
    {inputError && <p className="banner banner--error" role="status">{inputError}</p>}
    <div className="mt-space-4 grid grid-cols-[1fr_2fr] gap-space-2">
      <Button variant="neutral" className="min-w-0 whitespace-normal px-2 text-caption" disabled={locked} onClick={onClose}>원문으로 돌아가기</Button>
      <Button className="min-w-0 px-2 max-[374px]:text-caption" disabled={locked || !valid} onClick={() => {
        setShowVoice(false);
        setFirstQuestion(false);
        setVoiceFailure(null);
        if (pending.length) setField(pending[0]); else onFinish();
      }}>{pending.length ? "다음 질문" : "입력 내용 확인하기"}</Button>
    </div>
  </div>;
}
