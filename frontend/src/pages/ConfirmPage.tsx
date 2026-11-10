import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { CounterpartySuggestion } from "../components/CounterpartySuggestion";
import { CandidateGuidance } from "../components/CandidateGuidance";
import { stopGuidanceAudio } from "../utils/guidanceAudio";
import { CandidateFieldsForm } from "../components/CandidateFieldsForm";
import { ErrorDialog } from "../components/ErrorDialog";
import { ConfirmView } from "../ui/ConfirmView";
import { BottomSheet } from "../ui/BottomSheet";
import { Button } from "../ui/Button";
import { candidateView } from "../utils/viewModels";
import { AppTabs } from "../components/AppTabs";
import { RequestError } from "../components/RequestError";
import { LeaveDraftDialog } from "../ui/LeaveDraftDialog";
import { useDraftLeaveGuard } from "../utils/useDraftLeaveGuard";
import { useModalFocus } from "../ui/useModalFocus";
import { guardCandidateResponse, useAuthStore, useCandidateStore, useToastStore } from "../stores/appStore";
import { currentSessionEpoch, getEntry, sendFeedback } from "../api/client";
import { describeApiFailure, type ApiFailure } from "../utils/apiError";
import { kstRegistrationLabel } from "../utils/useLedgerData";
import { invalidateStartupLedger } from "../utils/startupLedger";
import {
  clearCandidateDraft,
  createCandidateDraft,
  draftToCandidateFields,
  draftProblems,
  recalculateDraftTotal,
  restoreCandidateDraft,
  saveCandidateDraft,
  updateDraftItem,
  type CandidateDraft,
  type EditableItem,
} from "../utils/candidateDraft";

const blankItem: EditableItem = { name: "", qty: "", unit: "", unit_price: "", amount: "" };

/** Let the user inspect and edit a parsed candidate without confirming a ledger entry. */
export function ConfirmPage() {
  const sessionKey = useAuthStore((state) => `${state.userId}:${currentSessionEpoch()}`);
  return <ConfirmSession key={sessionKey} />;
}

/** Pending feedback and local editors belong to the session that opened the candidate. */
function ConfirmSession() {
  const navigate = useNavigate();
  const candidate = useCandidateStore((state) => state.candidate);
  const hydrated = useCandidateStore((state) => state.hydrated);
  const setCandidate = useCandidateStore((state) => state.setCandidate);
  const restoreError = useCandidateStore((state) => state.restoreError);
  const [savedEdits, setSavedEdits] = useState<CandidateDraft | null>(null);
  const [busy, setBusy] = useState(false);
  const decisionRevision = useRef(0);
  const [error, setError] = useState<ApiFailure | null>(null);
  const [draft, setDraft] = useState<CandidateDraft | null>(null);
  const draftRef = useRef<CandidateDraft | null>(null);
  const [rejectOpen, setRejectOpen] = useState(false);
  const editForm = useRef<HTMLFieldSetElement>(null);
  const restoreDialog = useRef<HTMLDivElement>(null);
  const rejectDialog = useRef<HTMLDivElement>(null);
  useModalFocus(!!savedEdits, restoreDialog);
  useModalFocus(rejectOpen, rejectDialog);
  const lastDecision = useRef<"confirm" | "reject">("confirm");
  const [editingItem, setEditingItem] = useState<number | null>(null);
  const [fullEdit, setFullEdit] = useState(false);
  const [date, setDate] = useState<string | null>(null);
  const userId = useAuthStore((state) => state.userId);
  const authReady = useAuthStore((state) => state.ready);
  const leaveGuard = useDraftLeaveGuard(!!candidate && !!userId);
  useEffect(() => {
    if (fullEdit && editingItem !== null) document.getElementById(`candidate-item-${editingItem}`)?.scrollIntoView({ block: "center" });
  }, [fullEdit, editingItem]);

  useEffect(() => {
    let cancelled = false;
    setDate(null);
    if (!candidate || !userId) return;
    const showDate = (createdAt: string) => {
      if (!cancelled) setDate(kstRegistrationLabel(createdAt));
    };
    if (candidate.created_at) showDate(candidate.created_at);
    else void getEntry(candidate.entry_id).then((entry) => showDate(entry.created_at))
      .catch(() => { if (!cancelled) setDate("등록일 확인 실패"); });
    return () => { cancelled = true; };
  }, [candidate?.entry_id, candidate?.created_at, userId]);

  useEffect(() => {
    decisionRevision.current++;
    setBusy(false);
    setSavedEdits(null); setFullEdit(false); setEditingItem(null); setRejectOpen(false); setError(null);
    if (hydrated && candidate) {
      const restored = restoreCandidateDraft(candidate);
      const original = createCandidateDraft(candidate, candidate.entry_id);
      setSavedEdits(JSON.stringify(restored) !== JSON.stringify(original) ? restored : null);
      draftRef.current = restored;
      setDraft(original);
    } else { draftRef.current = null; setDraft(null); }
    return () => { decisionRevision.current++; };
  }, [candidate, hydrated]);

  useEffect(() => {
    if (!draft || savedEdits) return;
    const timer = window.setTimeout(() => saveCandidateDraft(draft), 400);
    return () => window.clearTimeout(timer);
  }, [draft, savedEdits]);

  useEffect(() => {
    const flush = () => {
      if (draftRef.current && useCandidateStore.getState().candidate?.entry_id === draftRef.current.candidateId) saveCandidateDraft(draftRef.current);
    };
    const beforeUnload = () => {
      if (!draftRef.current) return;
      flush();
    };
    window.addEventListener("pagehide", flush);
    window.addEventListener("beforeunload", beforeUnload);
    return () => {
      window.removeEventListener("pagehide", flush);
      window.removeEventListener("beforeunload", beforeUnload);
      flush();
    };
  }, []);

  if (authReady && !userId) return <section className="page"><h1 className="page__title">로그인이 필요합니다</h1><p>작성 중인 내용은 보관했습니다.</p><Link className="btn btn--link" to="/login">로그인하기</Link></section>;
  if (!authReady || !hydrated || (candidate && (!draft || draft.candidateId !== candidate.entry_id))) {
    return <p className="notice" role="status">후보를 불러오는 중입니다.</p>;
  }
  if (!candidate || !draft) {
    return (
      <section className="page">
        <h1 className="page__title">확인할 후보가 없습니다</h1>
        <p className="page__hint">거래 내용을 입력하면 확인할 후보가 여기에 표시됩니다.</p>
        {restoreError && <><p role="alert">{restoreError}</p><Link className="text-link" to="/record">거래 입력으로 돌아가기</Link><Button  variant="secondary" onClick={() => useCandidateStore.getState().hydrateFromStorage()}>다시 불러오기</Button></>}
        <Link className="btn btn--link" to="/record">거래 입력하기</Link>
      </section>
    );
  }

  const activeDraft = draft;
  const problems = draftProblems(activeDraft);
  const issues = problems.map((problem) => problem.message);
  function finishEditing() {
    if (problems.length) {
      const selector = problems[0].field === "items" ? "[data-add-item]" : '[aria-invalid="true"]';
      const field = editForm.current?.querySelector<HTMLElement>(selector);
      field?.scrollIntoView({ block: "center" });
      field?.focus();
      return;
    }
    setFullEdit(false);
  }

  function commitDraft(next: CandidateDraft) {
    draftRef.current = next;
    setDraft(next);
  }

  async function submitDecision(action: "confirm" | "reject") {
    if (busy || !candidate || !draft || (action === "confirm" && problems.length > 0)) return;
    setBusy(true);
    setError(null);
    lastDecision.current = action;
    const owner = userId;
    const epoch = currentSessionEpoch();
    const request = ++decisionRevision.current;
    const candidateCurrent = guardCandidateResponse();
    const isCurrent = () => request === decisionRevision.current && candidateCurrent() && useAuthStore.getState().userId === owner &&
      currentSessionEpoch() === epoch &&
      useCandidateStore.getState().candidate?.entry_id === candidate.entry_id;
    try {
      const fields = draftToCandidateFields(draft);
      const original = draftToCandidateFields(createCandidateDraft(candidate, candidate.entry_id));
      const changed = JSON.stringify(fields) !== JSON.stringify(original);
      const confirmed = await sendFeedback(candidate.entry_id, action === "reject" ? { action } : changed ? { action: "correct", corrected: fields } : { action });
      if (!isCurrent()) return;
      if (action !== "reject") invalidateStartupLedger();
      draftRef.current = null;
      clearCandidateDraft();
      setCandidate(null);
      useToastStore.getState().show(action === "reject" ? "후보를 버렸습니다." : "장부에 확정했습니다.");
      leaveGuard.allowNavigation();
      const confirmation = action !== "reject" && confirmed.status === "confirmed" && confirmed.credit === true && confirmed.counterparty
        ? { counterparty: confirmed.counterparty, owner, epoch } : undefined;
      navigate(action === "reject" ? "/record" : "/ledger", { state: confirmation ? { confirmation } : null });
    } catch (failure) { if (isCurrent()) { setRejectOpen(false); setError(describeApiFailure(failure)); } }
    finally { if (isCurrent()) setBusy(false); }
  }

  function discardCandidate() {
    setError(null);
    setRejectOpen(true);
  }

  return (
    <section onClickCapture={stopGuidanceAudio} onChangeCapture={stopGuidanceAudio}>
      <fieldset className="design-fields" disabled={busy || !!savedEdits}>
        <ConfirmView candidate={candidateView(activeDraft, candidate.utterance_text, `${date ?? "등록일 확인 중…"} · 매출`)}
          guidanceControls={<CandidateGuidance draft={activeDraft} utterance={candidate.utterance_text} identity={`${userId}:${currentSessionEpoch()}:${candidate.entry_id}`} active={!!userId && !busy && !savedEdits && !fullEdit && !rejectOpen && !leaveGuard.open && issues.length === 0} />}
          counterpartySuggestion={<CounterpartySuggestion key={`${userId}:${currentSessionEpoch()}:${candidate.entry_id}`}
            value={activeDraft.counterparty} onSelect={(counterparty) => commitDraft({ ...activeDraft, counterparty })}
            onEdit={() => { setEditingItem(null); setFullEdit(true); }} />}
          validationMessage={fullEdit ? undefined : problems.find((problem) => problem.field !== "credit")?.message}
          confirming={busy} disabled={issues.length > 0 || !!savedEdits}
          onBack={() => navigate("/record")} onAnswer={() => {}}
          onCreditChange={(credit) => commitDraft({ ...activeDraft, credit })}
          onEditItem={(index) => { setEditingItem(index); setFullEdit(true); }}
          onEdit={() => { setEditingItem(null); setFullEdit(true); }} onDiscard={discardCandidate}
          onConfirm={() => void submitDecision("confirm")} bottomTab={<AppTabs embedded />} />
      </fieldset>
      {error?.kind === "input" && <ErrorDialog message={error.message} onClose={() => setError(null)} />}
      {error && error.kind !== "input" && <RequestError failure={error} retry={() => void submitDecision(lastDecision.current)} />}
      <BottomSheet open={fullEdit} title={editingItem === null ? "내용 수정하기" : `${editingItem + 1}번째 품목 수정`}
        onClose={() => { if (!busy) setFullEdit(false); }} footer={<><p role="status" className={issues.length ? "mb-space-2 text-caption text-danger" : "sr-only"}>{issues[0] ?? ""}</p><Button disabled={busy} onClick={finishEditing}>수정 완료</Button></>}>
        <fieldset ref={editForm} className="design-fields" disabled={busy}>
          <CandidateFieldsForm draft={activeDraft} problems={problems}
            onField={(field, value) => commitDraft({ ...activeDraft, [field]: value })}
            onItem={(index, field, value) => commitDraft(updateDraftItem(activeDraft, index, field, value))}
            onAddItem={() => commitDraft({ ...activeDraft, items: [...activeDraft.items, { ...blankItem }] })}
            onRemoveItem={(index) => commitDraft(recalculateDraftTotal({ ...activeDraft, items: activeDraft.items.filter((_, itemIndex) => itemIndex !== index) }))} />
        </fieldset>
      </BottomSheet>
      <LeaveDraftDialog open={leaveGuard.open} onStay={leaveGuard.stay} onLeave={leaveGuard.leave} />
      {savedEdits && <div className="dialog-backdrop"><div ref={restoreDialog} tabIndex={-1} className="dialog" role="dialog" aria-modal="true" aria-labelledby="restore-title">
        <h2 id="restore-title">저장하지 않은 수정 내용이 있습니다</h2><p>서버에서 후보를 다시 불러왔습니다. 이 기기의 수정 내용을 적용할까요?</p>
        <div className="dialog__actions"><Button  variant="secondary" onClick={() => { commitDraft(activeDraft); setSavedEdits(null); saveCandidateDraft(activeDraft); }}>서버 내역 사용</Button>
          <Button  onClick={() => { commitDraft(savedEdits); setSavedEdits(null); }}>수정 내용 적용</Button></div>
      </div></div>}
      {rejectOpen && <div className="dialog-backdrop"><div ref={rejectDialog} tabIndex={-1} className="dialog" role="dialog" aria-modal="true" aria-labelledby="reject-title">
        <h2 id="reject-title">이 후보를 버릴까요?</h2><p>입력 내용은 장부에 반영되지 않습니다.</p>
        {error && <p role="alert">{error.message}</p>}
        <div className="dialog__actions"><Button  variant="secondary" disabled={busy} onClick={() => setRejectOpen(false)}>계속 확인하기</Button>
          <Button  disabled={busy} onClick={() => void submitDecision("reject")}>{busy ? "처리 중…" : "후보 버리기"}</Button></div>
      </div></div>}
    </section>
  );
}
