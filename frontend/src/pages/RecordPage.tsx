import { Button } from "../ui/Button";
import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { currentSessionEpoch, fetchDevToken, getSession, parseCorrectedCandidate, parseUtterance } from "../api/client";
import { ErrorDialog } from "../components/ErrorDialog";
import { RepairDialog } from "../components/RepairDialog";
import { VoiceRecorder, type VoiceRecorderHandle } from "../components/VoiceRecorder";
import { readUtteranceDraft, saveUtteranceDraft } from "../utils/draftStorage";
import type { CandidateSnapshot, ParseCandidate } from "../types/api";
import { guardCandidateResponse, useAuthStore, useCandidateStore } from "../stores/appStore";
import { describeApiFailure, errorNoticeKind, type ApiFailure } from "../utils/apiError";
import { useDraftLeaveGuard } from "../utils/useDraftLeaveGuard";
import { LeaveDraftDialog } from "../ui/LeaveDraftDialog";
import { clearCandidateDraft, createRepairDraft, draftToCandidateFields, type CandidateDraft } from "../utils/candidateDraft";
import { clearRepairDraft, readRepairDraft, saveRepairDraft, type RepairProgress, type RepairSnapshot } from "../utils/repairStorage";

import { RecordHomeView } from "../ui/RecordHomeView";
import { RetryNotice, type RetryCode } from "../ui/RetryNotice";
import { RequestError } from "../components/RequestError";
import { useLedgerData, kstToday } from "../utils/useLedgerData";
import { ledgerEntryView, receivableNote } from "../utils/viewModels";
import { daysAgoLabel } from "../utils/creditMessage";
import { prepareGuidanceAudio, stopGuidanceAudio } from "../utils/guidanceAudio";
import { NonSaleNotice } from "../components/NonSaleNotice";
import { ErrorNotice } from "../ui/ErrorNotice";
import { useExistingCreditChoice } from "../utils/useExistingCreditChoice";
import { MoveCreditDialog } from "../components/MoveCreditDialog";
import { ConfirmDialog } from "../ui/ConfirmDialog";

const configuredLimit = Number(import.meta.env.VITE_INPUT_MAX_LENGTH);
const INPUT_MAX_LENGTH = Number.isInteger(configuredLimit) && configuredLimit > 0 ? configuredLimit : 500;
type NewInput = { kind: "voice" } | { kind: "text"; text: string };
interface PendingReplacement { action: NewInput; candidate: CandidateSnapshot; owner: string | null; epoch: number }

/** Text entry stays available when voice input is unavailable. */
export function RecordPage() {
  const navigate = useNavigate();
  const recorderControl = useRef<VoiceRecorderHandle>(null);
  const [typing, setTyping] = useState(() => !!readUtteranceDraft());
  const { ledger, unpaid, busy: loadingLedger, error: ledgerError, reload } = useLedgerData(kstToday().slice(0, 7));
  const openText = () => setTyping(true);
  const [utterance, setUtterance] = useState(readUtteranceDraft);
  const utteranceRef = useRef(utterance);
  const utteranceDirtyRef = useRef(false);
  const [voiceBusy, setVoiceBusy] = useState(false);
  const [audioFailure, setAudioFailure] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [repairUtterance, setRepairUtterance] = useState("");
  const [failure, setFailure] = useState<ApiFailure | null>(null);
  const [voiceTranscript, setVoiceTranscript] = useState<string | null>(null);
  const [repairDraft, setRepairDraft] = useState<CandidateDraft | null>(null);
  const [repairError, setRepairError] = useState<string | null>(null);
  const [repairFailure, setRepairFailure] = useState<ApiFailure | null>(null);
  const [repairOf, setRepairOf] = useState<string | undefined>();
  const [repairProgress, setRepairProgress] = useState<RepairProgress>({});
  const [savedRepair, setSavedRepair] = useState<RepairSnapshot | null>(() => readRepairDraft(useAuthStore.getState().userId));
  const [busy, setBusy] = useState(false);
  const repairSubmitting = useRef(false);
  const mounted = useRef(true);
  const inputRevision = useRef(0);
  const sessionEpoch = currentSessionEpoch();
  useEffect(() => {
    const clearNonSale = () => setFailure((current) => current?.errorCode === "NOT_A_SALE" ? null : current);
    window.addEventListener("echobill:session-changed", clearNonSale);
    return () => window.removeEventListener("echobill:session-changed", clearNonSale);
  }, []);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  const accessToken = useAuthStore((s) => s.accessToken);
  const userId = useAuthStore((s) => s.userId);
  const [skippedExisting, skipExisting] = useExistingCreditChoice(userId);
  const setSession = useAuthStore((s) => s.setSession);
  const candidate = useCandidateStore((s) => s.candidate);
  const [replacement, setReplacement] = useState<PendingReplacement | null>(null);
  useEffect(() => { setReplacement(null); }, [sessionEpoch, candidate]);
  useEffect(() => { setBusy(false); repairSubmitting.current = false; }, [sessionEpoch, candidate]);
  const setCandidate = useCandidateStore((s) => s.setCandidate);
  const leaveGuard = useDraftLeaveGuard(!!utterance.trim() || !!repairDraft || !!savedRepair || voiceBusy, returnToRecordHome);
  useEffect(() => {
    if (userId && !repairDraft) setSavedRepair(readRepairDraft(userId));
  }, [userId]);
  // External candidate-store updates can render before local repair state clears.
  // Keep completed/discarded work closed through those renders and effect cleanup.
  const repairClosed = useRef(false);
  const currentRepair = useRef<RepairSnapshot | null>(null);
  currentRepair.current = !repairClosed.current && userId && repairDraft && failure?.kind === "input" ? {
    owner: userId, draft: repairDraft, utterance: repairUtterance, transcript: voiceTranscript,
    repairOf, failure: { kind: "input", message: failure.message, errorCode: failure.errorCode, problemFields: failure.problemFields }, progress: repairProgress,
  } : null;
  useEffect(() => {
    const save = () => { if (currentRepair.current) saveRepairDraft(currentRepair.current); };
    const timer = window.setTimeout(save, 400);
    window.addEventListener("pagehide", save);
    return () => { window.clearTimeout(timer); window.removeEventListener("pagehide", save); save(); };
  }, [repairDraft, repairUtterance, repairOf, repairProgress, failure, voiceTranscript, userId]);

  /** Close the editor while keeping its recovery snapshot on the record home. */
  function returnToRecordHome() {
    inputRevision.current++;
    repairSubmitting.current = false;
    setBusy(false);
    stopGuidanceAudio();
    if (currentRepair.current) {
      saveRepairDraft(currentRepair.current);
      setSavedRepair(currentRepair.current);
    }
    setFailure(null);
    setRepairDraft(null);
    setRepairError(null);
    setRepairFailure(null);
    setVoiceTranscript(null);
    setTyping(false);
  }

  function updateUtterance(value: string) {
    if (utteranceRef.current !== value) utteranceDirtyRef.current = true;
    setUtterance(value);
    utteranceRef.current = value;
  }

  useEffect(() => {
    const save = () => {
      if (!utteranceDirtyRef.current) return;
      saveUtteranceDraft(utteranceRef.current);
      utteranceDirtyRef.current = false;
    };
    const timer = window.setTimeout(save, 400);
    return () => window.clearTimeout(timer);
  }, [utterance]);
  useEffect(() => {
    const save = () => {
      if (!utteranceDirtyRef.current) return;
      saveUtteranceDraft(utteranceRef.current);
      utteranceDirtyRef.current = false;
    };
    const endSession = () => {
      utteranceDirtyRef.current = false;
      utteranceRef.current = "";
      setUtterance("");
      setVoiceTranscript(null);
      setFailure(null);
      setRepairDraft(null);
      setRepairUtterance("");
      setRepairError(null);
      setRepairFailure(null);
      setRepairOf(undefined);
      setSavedRepair(null);
      setRepairProgress({});
      currentRepair.current = null;
    };
    window.addEventListener("pagehide", save);
    window.addEventListener("echobill:session-ended", endSession);
    return () => { window.removeEventListener("pagehide", save); window.removeEventListener("echobill:session-ended", endSession); save(); };
  }, []);

  async function ensureAuth() {
    if (userId || accessToken) return;
    if (import.meta.env.DEV) {
      const token = await fetchDevToken();
      setSession(token.user_id, token.access_token);
      return;
    }
    try {
      const session = await getSession();
      useAuthStore.getState().setCookieSession(session.user_id, session.voice_answer_fields, session.ai_consent);
    } catch (error) {
      if (describeApiFailure(error).kind === "auth") navigate("/login");
      throw error;
    }
  }

  /** Close completed repair work before state updates can run an autosave cleanup. */
  function finishRepair() {
    repairClosed.current = true;
    currentRepair.current = null;
    setSavedRepair(null);
    setRepairDraft(null);
    setFailure(null);
    clearRepairDraft();
    updateUtterance("");
    saveUtteranceDraft("");
    utteranceDirtyRef.current = false;
    leaveGuard.allowNavigation();
  }

  function acceptCandidate(result: ParseCandidate) {
    finishRepair();
    clearCandidateDraft();
    setCandidate(result);
    navigate("/record/confirm");
  }

  function showVoiceFailure(result: ApiFailure) {
    repairClosed.current = false;
    setAudioFailure(true);
    setRepairProgress({});
    setRepairOf(result.requestKey);
    setFailure(result);
    const transcript = result.kind === "input" ? result.utteranceText : undefined;
    setVoiceTranscript(transcript ?? null);
    setRepairUtterance(transcript ?? "");
    if (transcript) {
      updateUtterance(transcript);
    }
    setRepairDraft(result.kind === "input" && result.partialCandidate ? createRepairDraft(result.partialCandidate, result.problemFields ?? []) : null);
    if (result.kind === "auth") navigate("/login");
  }

  function showFailure(error: unknown) {
    repairClosed.current = false;
    setRepairProgress({});
    const result = describeApiFailure(error);
    setRepairOf(result.requestKey);
    setFailure(result);
    setRepairDraft(result.kind === "input" && result.errorCode !== "UNSUPPORTED_TRANSACTION" && result.partialCandidate
      ? createRepairDraft(result.partialCandidate, result.problemFields ?? []) : null);
  }

  /** Ignore completed input requests after navigation, session replacement, or newer candidate work. */
  function guardInputResponse() {
    const revision = inputRevision.current;
    const epoch = currentSessionEpoch();
    const owner = useAuthStore.getState().userId;
    const candidateCurrent = guardCandidateResponse();
    return () => mounted.current && revision === inputRevision.current && epoch === currentSessionEpoch() && owner === useAuthStore.getState().userId && candidateCurrent();
  }

  /** Parse a single utterance, then move to the editable candidate screen. */
  async function submitText(text: string) {
    if (busy || voiceBusy || !text.trim() || text.length > INPUT_MAX_LENGTH) return;
    if (askBeforeReplacing({ kind: "text", text })) return;
    prepareGuidanceAudio();
    setAudioFailure(false);
    setFailure(null);
    setVoiceTranscript(null);
    setRepairDraft(null);
    setRepairUtterance(text.trim());
    clearRepairDraft();
    setSavedRepair(null);
    setBusy(true);
    const revision = inputRevision.current;
    let current = guardInputResponse();
    const hadOwner = !!useAuthStore.getState().userId;
    try {
      await ensureAuth();
      if (hadOwner && !current()) return;
      if (!mounted.current || revision !== inputRevision.current) return;
      current = guardInputResponse();
      const result = await parseUtterance(text.trim());
      if (!current()) return;
      acceptCandidate(result);
    } catch (error) {
      if (!current()) return;
      showFailure(error);
    } finally {
      if (current()) setBusy(false);
    }
  }

  /** Preserve the pending candidate until its owner explicitly chooses new input. */
  function askBeforeReplacing(action: NewInput): boolean {
    const current = useCandidateStore.getState().candidate;
    if (!current) return false;
    stopGuidanceAudio();
    setReplacement({ action, candidate: current, owner: useAuthStore.getState().userId, epoch: currentSessionEpoch() });
    return true;
  }

  /** A click on Resume also unlocks server speech before navigating. */
  function resumeCandidate() {
    setReplacement(null);
    prepareGuidanceAudio();
    leaveGuard.allowNavigation();
    navigate("/record/confirm");
  }

  /** Check pending work before opening the microphone. */
  function startRecording() {
    if (busy || voiceBusy || askBeforeReplacing({ kind: "voice" })) return;
    setFailure(null);
    recorderControl.current?.start();
  }

  /** Stale dialogs cannot discard another candidate or work from a newer session. */
  function replaceCandidate() {
    setReplacement(null);
    if (!replacement || replacement.owner !== useAuthStore.getState().userId ||
        replacement.epoch !== currentSessionEpoch() || replacement.candidate !== useCandidateStore.getState().candidate) return;
    clearCandidateDraft();
    setCandidate(null);
    if (replacement.action.kind === "voice") startRecording();
    else void submitText(replacement.action.text);
  }

  /** Retry the same corrections with their request identity intact; preserve edits on failure. */
  async function submitRepair() {
    if (!repairDraft || busy || repairSubmitting.current) return;
    if (!repairUtterance.trim()) {
      setRepairError("원문이 없습니다. 원문으로 돌아가 거래 내용을 입력해 주세요.");
      return;
    }
    repairSubmitting.current = true;
    setRepairError(null);
    setBusy(true);
    const current = guardInputResponse();
    try {
      const result = await parseCorrectedCandidate(repairUtterance, draftToCandidateFields(repairDraft), repairOf);
      if (!current()) return;
      acceptCandidate(result);
      setFailure(null);
      setRepairDraft(null);
    } catch (error) {
      if (!current()) return;
      if (error instanceof Error && error.message === "SERVER_MANUAL_PARSE_UNAVAILABLE") {
        setRepairError("서버의 보완 제출 기능이 아직 준비되지 않았습니다. 수정 내용은 화면에 남아 있습니다.");
      } else {
        const result = describeApiFailure(error);
        if (result.kind === "conflict" && result.errorCode === "REPAIR_ALREADY_CONFIRMED") {
          finishRepair();
          setRepairError(null);
          setRepairFailure(result);
          return;
        }
        if (result.kind === "input") { setFailure(result); setRepairFailure(null); }
        else setRepairFailure(result);
        setRepairError(result.message);
      }
    } finally {
      if (current()) { repairSubmitting.current = false; setBusy(false); }
    }
  }

  /** A non-sale action is explicit navigation, not a repair or a new ledger candidate. */
  function openNonSaleDestination() {
    const redirect = failure?.redirect;
    stopGuidanceAudio();
    finishRepair();
    setVoiceTranscript(null);
    setTyping(false);
    if (redirect === "moved_credit") setMoveOpen(true);
    else if (redirect === "inquiry") window.scrollTo?.(0, 0);
    else navigate("/ledger", { state: redirect === "payment" ? { openPayment: { owner: userId, epoch: currentSessionEpoch() } } : undefined });
  }

  const nonSale = failure?.kind === "input" && failure.errorCode === "NOT_A_SALE";
  const noticeKind = failure ? errorNoticeKind(failure) : null;
  return (
    <section className="record-page">
      <RecordHomeView signedIn={!!userId} loadFailed={!!ledgerError} unpaidTotal={unpaid?.total_unpaid ?? null} unpaidNote={unpaid ? receivableNote(unpaid) : undefined}
        firstUse={unpaid ? unpaid.has_confirmed_entries === false : undefined}
        onMoveExisting={userId ? () => setMoveOpen(true) : undefined}
        onSkipExisting={userId && !skippedExisting ? () => { skipExisting(); setFailure(null); recorderControl.current?.start(); } : undefined}
        cashCount={ledger?.entries.filter((row) => row.status === "confirmed" && row.credit === false).length}
        recent={(ledger?.entries ?? []).filter((row) => row.status === "confirmed").slice().sort((a,b) => b.created_at.localeCompare(a.created_at)).slice(0,3)
          .map((entry) => ({ ...ledgerEntryView(entry), dateLabel: entry.moved ? "날짜는 어림값" : daysAgoLabel(entry.kst_date, kstToday()) }))}
        loading={loadingLedger} text={utterance} onTextChange={updateUtterance} typing={typing} onTypingChange={setTyping}
        maxLength={INPUT_MAX_LENGTH} busy={busy || voiceBusy} onSubmitText={(text) => void submitText(text)}
        onRecord={startRecording} onOpenMyPage={() => navigate("/my-page")}
        draftLabel={candidate && userId ? candidate.counterparty ?? "작성 중인 후보" : null} onResumeDraft={resumeCandidate}
        onOpenLedger={() => navigate("/ledger")} onOpenEntry={(id) => {
          const entry = ledger?.entries.find((row) => row.entry_id === id);
          navigate("/ledger", { state: { date: entry?.kst_date, counterparty: entry?.counterparty } });
        }} />
      {ledgerError && <RequestError failure={ledgerError} retry={reload} />}
      {moveOpen && <MoveCreditDialog onClose={() => setMoveOpen(false)} onMoved={reload} />}
      {savedRepair && <div className="notice" role="status"><p>작성 중인 보완 내용이 있습니다. 이어서 입력할까요?</p>
        <Button  type="button" disabled={busy || voiceBusy} onClick={() => {
          if (savedRepair.owner !== useAuthStore.getState().userId) { clearRepairDraft(); setSavedRepair(null); return; }
          prepareGuidanceAudio();
          repairClosed.current = false;
          setRepairDraft(savedRepair.draft); setFailure(savedRepair.failure); setRepairUtterance(savedRepair.utterance);
          setVoiceTranscript(savedRepair.transcript); setRepairOf(savedRepair.repairOf); setRepairProgress(savedRepair.progress); setSavedRepair(null);
        }}>이어서 입력하기</Button>
        <button className="text-link" type="button" disabled={busy || voiceBusy} onClick={() => { clearRepairDraft(); setSavedRepair(null); }}>보완 내용 버리기</button>
      </div>}
      <VoiceRecorder controlRef={recorderControl} onCandidate={acceptCandidate} onFailure={showVoiceFailure} ensureAuth={ensureAuth}
        onFallback={() => openText()} onBusy={setVoiceBusy} disabled={busy} />

      {nonSale && <NonSaleNotice message={failure.message} redirect={failure.redirect}
        onContinue={openNonSaleDestination} onClose={() => { stopGuidanceAudio(); setFailure(null); }} />}
      {voiceTranscript && !nonSale && !repairDraft && failure?.kind === "input" && !["UNSUPPORTED_TRANSACTION", "EXCEED_MAX_ITEMS", "MULTIPLE_COUNTERPARTIES_DETECTED"].includes(failure.errorCode ?? "") &&
        <p className="notice">이렇게 들렸어요: {voiceTranscript} · 글자로 고쳐서 다시 보내 주세요.</p>}
      {failure && noticeKind && <ErrorNotice kind={noticeKind} retryAfter={failure.retryAfter}
        onClose={() => setFailure(null)} onType={() => { setFailure(null); openText(); }} onRetry={() => {
          setFailure(null);
          if (noticeKind === "conflict" || noticeKind === "alreadyConfirmed") navigate("/ledger");
          else if (noticeKind === "notFound") navigate("/record");
          else if (noticeKind === "tooLong" || noticeKind === "expired") recorderControl.current?.start();
          else if (audioFailure) recorderControl.current?.retry(); else void submitText(utterance);
        }} />}
      {failure && failure.kind !== "input" && !noticeKind && <RequestError failure={failure} retry={() => {
        if (audioFailure) recorderControl.current?.retry(); else void submitText(utterance);
      }} />}
      {failure?.kind === "input" && ["UNSUPPORTED_TRANSACTION", "EXCEED_MAX_ITEMS", "MULTIPLE_COUNTERPARTIES_DETECTED"].includes(failure.errorCode ?? "") &&
        <RetryNotice code={failure.errorCode as RetryCode} heard={voiceTranscript ?? utterance} fromVoice={!!voiceTranscript}
          onClose={() => { setFailure(null); setRepairDraft(null); }} onType={() => { setFailure(null); openText(); }}
          onRetry={() => { setFailure(null); recorderControl.current?.start(); }} />}
      {failure?.kind === "input" && !nonSale && !["UNSUPPORTED_TRANSACTION", "EXCEED_MAX_ITEMS", "MULTIPLE_COUNTERPARTIES_DETECTED"].includes(failure.errorCode ?? "") && !repairDraft && !voiceTranscript && (
        <ErrorDialog message={failure.message} onClose={() => { setFailure(null); openText(); }} />
      )}
      {failure?.kind === "input" && !nonSale && !["UNSUPPORTED_TRANSACTION", "EXCEED_MAX_ITEMS", "MULTIPLE_COUNTERPARTIES_DETECTED"].includes(failure.errorCode ?? "") && repairDraft && (
        <RepairDialog
          message={failure.message}
          errorCode={failure.errorCode}
          transcript={voiceTranscript}
          problemFields={failure.problemFields ?? []}
          draft={repairDraft}
          busy={busy}
          submitError={repairError}
          progress={repairProgress}
          onProgress={setRepairProgress}
          onChange={setRepairDraft}
          onSubmit={submitRepair}
          onClose={() => { repairClosed.current = true; currentRepair.current = null; clearRepairDraft(); setFailure(null); setRepairDraft(null); setRepairError(null); setRepairFailure(null); openText(); }}
        />
      )}
      {repairFailure && <RequestError failure={repairFailure} retry={submitRepair} onDismiss={() => setRepairFailure(null)} />}
      <ConfirmDialog open={!!replacement} title="확인하지 않은 기록이 있어요" message="새로 기록하기 전에 이전 내용을 먼저 확인할까요?"
        cancelLabel="먼저 확인하기" confirmLabel="버리고 새로 입력" destructive
        onCancel={resumeCandidate} onConfirm={replaceCandidate} />
      {/* Keep the active navigation guard above recovery notices. */}
      <LeaveDraftDialog open={leaveGuard.open} onStay={leaveGuard.stay} onLeave={leaveGuard.leave} />
    </section>
  );
}
