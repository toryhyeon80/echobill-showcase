import { Button } from "../ui/Button";
import { useState } from "react";
import { BottomSheet } from "../ui/BottomSheet";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { CandidateFieldsForm } from "./CandidateFieldsForm";
import { ClarificationCard } from "./ClarificationCard";
import { arithmeticChoices, pendingClarifications } from "../utils/clarification";
import type { RepairProgress } from "../utils/repairStorage";
import { draftProblems, recalculateDraftTotal, updateDraftItem, type CandidateDraft, type EditableItem } from "../utils/candidateDraft";

interface Props {
  message: string;
  errorCode?: string;
  transcript?: string | null;
  problemFields: string[];
  draft: CandidateDraft;
  busy: boolean;
  submitError: string | null;
  onChange: (draft: CandidateDraft) => void;
  onSubmit: () => void;
  onClose: () => void;
  progress?: RepairProgress;
  onProgress?: (progress: RepairProgress) => void;
}

const blankItem: EditableItem = { name: "", qty: "", unit: "", unit_price: "", amount: "" };

/** Centered correction form for an unsaved 400 partial candidate. */
export function RepairDialog({ message, errorCode, transcript, problemFields, draft, busy, submitError, onChange, onSubmit, onClose, progress, onProgress }: Props) {
  const [amountChoices] = useState(() => errorCode === "AMOUNT_MISMATCH" ? arithmeticChoices(draft) : []);
  const [fullForm, setFullForm] = useState(() => progress?.fullForm === true || amountChoices.length !== 2 &&
    (errorCode !== "MISSING_REQUIRED_FIELD" || pendingClarifications(draft).length === 0 || draft.items.some((item) => !item.name.trim())));
  const [closing, setClosing] = useState(false);
  const problems = draftProblems(draft);
  const issues = problems.map((problem) => problem.message);
  return (
      <>
      <BottomSheet open title={fullForm ? "입력 내용을 고쳐 주세요" : "빠진 내용을 알려 주세요"} onClose={() => { if (!busy) setClosing(true); }}>
        <p id="repair-message" className={fullForm ? undefined : "visually-hidden"}>{message}</p>
        {fullForm && transcript && <details className="repair-transcript"><summary>이렇게 들렸어요</summary><p>{transcript}</p></details>}
        {fullForm && <p className="muted">고친 내용을 확인한 뒤 제출해 주세요.</p>}
        {!fullForm && <ClarificationCard draft={draft} busy={busy} transcript={transcript} amountChoices={amountChoices} initialField={progress?.field ?? (amountChoices.length === 2 ? "total_amount" : undefined)}
          onFieldChange={(field) => onProgress?.({ field, fullForm: false })} onChange={onChange}
          onFinish={() => {
            if (issues.length === 0) onSubmit();
            else { setFullForm(true); onProgress?.({ fullForm: true }); }
          }}
          onEditAll={() => { setFullForm(true); onProgress?.({ fullForm: true }); }} onClose={() => { if (!busy) setClosing(true); }} />}
        {fullForm && <fieldset className="candidate-review-fields" disabled={busy}><CandidateFieldsForm
          draft={draft}
          problemFields={problemFields}
          problems={problems}
          onField={(field, value) => onChange({ ...draft, [field]: value })}
          onItem={(index, field, value) => onChange(updateDraftItem(draft, index, field, value))}
          onAddItem={() => onChange({ ...draft, items: [...draft.items, { ...blankItem }] })}
          onRemoveItem={(index) => onChange(recalculateDraftTotal({ ...draft, items: draft.items.filter((_, itemIndex) => itemIndex !== index) }))}
        /></fieldset>}
        {fullForm && issues.length > 0 && <div className="banner banner--error" role="status"><strong>확인할 항목</strong><ul>{issues.map((issue) => <li key={issue}>{issue}</li>)}</ul></div>}
        {submitError && <p className="banner banner--error" role="alert">{submitError}</p>}
        {fullForm && <div className="dialog__actions">
          <Button  variant="secondary" type="button" onClick={() => setClosing(true)} disabled={busy}>원문으로 돌아가기</Button>
          {fullForm && <Button  type="button" onClick={onSubmit} disabled={busy || issues.length > 0}>{busy ? "확인 중…" : "수정 내용 제출"}</Button>}
        </div>}
      </BottomSheet>
      <ConfirmDialog open={closing} title="보완 내용을 버릴까요?" message="입력 중인 보완 내용이 지워져요. 원문은 남아 있어요."
        destructive confirmLabel="버리기" onCancel={() => setClosing(false)} onConfirm={onClose} />
      </>
  );
}
