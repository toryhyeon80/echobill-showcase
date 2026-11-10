import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { Link, useNavigate } from "react-router-dom";
import { currentSessionEpoch, logout } from "../api/client";
import { updateConsent } from "../api/consent";
import { createRequestKey } from "../utils/requestKey";
import { getBusinessInfo, registerBusinessInfo, type BusinessInfo, type BusinessInfoRequest } from "../api/tax";
import { useAuthStore, useCandidateStore, useToastStore } from "../stores/appStore";
import { clearSessionDrafts } from "../utils/draftStorage";
import { describeApiFailure, type ApiFailure } from "../utils/apiError";
import { MyPageView } from "../ui/MyPageView";
import { ConfirmDialog } from "../ui/ConfirmDialog";
import { ContactSheet } from "../ui/ContactSheet";
import { DocumentSheet, type DocumentKind } from "../ui/DocumentSheet";
import { MoveCreditDialog } from "../components/MoveCreditDialog";
import { BusinessInfoDialog } from "../components/BusinessInfoDialog";
import { RequestError } from "../components/RequestError";
import { saveCounterpartySuggestions, useCounterpartySuggestions } from "../utils/counterpartySuggestionSettings";
import { recordingCueEnabled, saveRecordingCueEnabled } from "../utils/recordingCue";
import { autoStopEnabled, saveAutoStopEnabled } from "../utils/silenceDetection";
import { guidanceSoundEnabled, prepareGuidanceAudio, saveGuidanceSoundEnabled, subscribeGuidanceSound } from "../utils/guidanceAudio";

/** Account, masked business information and server-confirmed optional AI consent. */
export function MyPage() {
  const sessionKey = useAuthStore((state) => `${state.userId}:${currentSessionEpoch()}`);
  return <MyPageSession key={sessionKey} />;
}

/** Keep reads, pending writes and dialogs within the session that opened them. */
function MyPageSession() {
  const navigate = useNavigate();
  const suggestionsEnabled = useCounterpartySuggestions();
  const guidanceEnabled = useSyncExternalStore(subscribeGuidanceSound, guidanceSoundEnabled, () => true);
  const [cueEnabled, setCueEnabled] = useState(recordingCueEnabled);
  const [silenceEnabled, setSilenceEnabled] = useState(autoStopEnabled);
  const userId = useAuthStore((state) => state.userId);
  const aiConsent = useAuthStore((state) => state.aiConsent);
  const [consentBusy, setConsentBusy] = useState(false);
  const [consentFailure, setConsentFailure] = useState<ApiFailure | null>(null);
  const consentPending = useRef<{ value: boolean; key: string } | null>(null);
  const consentInFlight = useRef(false);
  const consentRevision = useRef(0);
  const [business, setBusiness] = useState<BusinessInfo | null>(null);
  const [editing, setEditing] = useState(false);
  const [documentKind, setDocumentKind] = useState<DocumentKind | null>(null);
  const [contact, setContact] = useState(false);
  const [moveOpen, setMoveOpen] = useState(false);
  const [loggingOut, setLoggingOut] = useState(false);
  const [busy, setBusy] = useState(false);
  const [failure, setFailure] = useState<ApiFailure | null>(null);
  const [businessError, setBusinessError] = useState<string | null>(null);
  const [businessFailure, setBusinessFailure] = useState<ApiFailure | null>(null);
  const [revision, setRevision] = useState(0);
  const retryAction = useRef<"load" | "logout">("load");
  const epoch = useRef(0);
  const businessRead = useRef(0);
  useEffect(() => {
    const request = ++epoch.current;
    const read = ++businessRead.current;
    setBusiness(null); setEditing(false); setFailure(null); setBusinessError(null); setBusinessFailure(null); setBusy(false);
    if (userId) void getBusinessInfo().then((info) => { if (request === epoch.current && read === businessRead.current) setBusiness(info); })
      .catch((error: unknown) => { if (request === epoch.current && read === businessRead.current) { retryAction.current = "load"; setFailure(describeApiFailure(error)); } });
    return () => { epoch.current++; };
  }, [userId, revision]);
  useEffect(() => {
    consentRevision.current++;
    consentPending.current = null; consentInFlight.current = false;
    setConsentFailure(null); setConsentBusy(false);
    // Returning after a lost PATCH must read the actual server consent again.
    if (userId) void useAuthStore.getState().hydrateFromStorage();
    return () => { consentRevision.current++; };
  }, [userId]);
  async function saveConsent(value: boolean) {
    if (consentInFlight.current || !userId || aiConsent === null) return;
    const owner = userId;
    const revision = ++consentRevision.current;
    consentInFlight.current = true; setConsentBusy(true); setConsentFailure(null);
    try {
      const request = consentPending.current ?? { value, key: createRequestKey() };
      consentPending.current = request;
      const response = await updateConsent(request.value, request.key);
      if (revision !== consentRevision.current || owner !== useAuthStore.getState().userId) return;
      useAuthStore.getState().setAiConsent(response.ai_consent);
      consentPending.current = null;
      useToastStore.getState().show(response.ai_consent ? "AI 개선 동의를 켰어요" : "AI 개선 동의를 껐어요");
    } catch (error) {
      if (revision !== consentRevision.current || owner !== useAuthStore.getState().userId) return;
      const result = describeApiFailure(error);
      if (!["network", "server", "external", "pending", "auth", "rate"].includes(result.kind)) consentPending.current = null;
      setConsentFailure(result);
    } finally {
      if (revision === consentRevision.current && owner === useAuthStore.getState().userId) {
        consentInFlight.current = false; setConsentBusy(false);
      }
    }
  }
  async function saveBusiness(body: BusinessInfoRequest, key: string) {
    if (busy) return;
    businessRead.current++;
    const request = epoch.current;
    setBusy(true); setBusinessError(null); setBusinessFailure(null);
    try {
      const info = await registerBusinessInfo(body, key);
      if (request !== epoch.current) return;
      setBusiness(info); setEditing(false); setFailure(null);
      useToastStore.getState().show("사업자 정보를 저장했어요");
    } catch (error) { if (request === epoch.current) { const result = describeApiFailure(error); setBusinessError(result.message); setBusinessFailure(result); } }
    finally { if (request === epoch.current) setBusy(false); }
  }
  async function endSession() {
    if (busy) return;
    const request = epoch.current;
    setBusy(true); setFailure(null);
    retryAction.current = "logout";
    try {
      await logout();
      if (request !== epoch.current) return;
      window.dispatchEvent(new Event("echobill:session-ended"));
      useAuthStore.getState().setCookieSession(null);
      useCandidateStore.getState().setCandidate(null);
      clearSessionDrafts(); useToastStore.getState().clear();
      navigate("/login", { replace: true });
    } catch (error) { if (request === epoch.current) { setLoggingOut(false); setFailure(describeApiFailure(error)); } }
    finally { if (request === epoch.current) setBusy(false); }
  }
  if (!userId) return <section className="page"><h1>로그인이 필요해요</h1><Link className="btn" to="/login">로그인하기</Link></section>;
  return <>
    {consentFailure && <RequestError failure={consentFailure} retry={() => void saveConsent(consentPending.current?.value ?? !aiConsent)} />}
    {failure && <RequestError failure={failure} retry={() => { if (busy) return; if (retryAction.current === "logout") void endSession(); else setRevision((value) => value + 1); }} />}
    <MyPageView business={business?.registered ? { name: business.business_name ?? "내 사업자", numberMasked: business.business_number_masked ?? "등록됨" } : null}
      guidanceSound={guidanceEnabled} onGuidanceSoundChange={(value) => { saveGuidanceSoundEnabled(value); if (value) prepareGuidanceAudio(); }}
      recordingCue={cueEnabled} onRecordingCueChange={(value) => { saveRecordingCueEnabled(value); setCueEnabled(value); }}
      autoStopRecording={silenceEnabled} onAutoStopRecordingChange={(value) => { saveAutoStopEnabled(value); setSilenceEnabled(value); }}
      counterpartySuggestions={suggestionsEnabled} onCounterpartySuggestionsChange={saveCounterpartySuggestions}
      showAiConsent={aiConsent !== null} aiConsent={aiConsent ?? false} consentBusy={consentBusy || consentPending.current !== null}
      onAiConsentChange={(value) => void saveConsent(value)} onBack={() => (window.history.state?.idx > 0 ? navigate(-1) : navigate("/record"))}
      onEditBusiness={() => { setBusinessError(null); setBusinessFailure(null); setEditing(true); }} onOpenPrivacy={() => setDocumentKind("privacy")} onOpenTerms={() => setDocumentKind("terms")} onMoveExisting={() => setMoveOpen(true)}
      onContact={() => setContact(true)} onRequestWithdrawal={() => setContact(true)} onLogout={() => setLoggingOut(true)} footnote="말로해! 팀 · 에코빌 v0.1" />
    <DocumentSheet doc={documentKind} onClose={() => setDocumentKind(null)} />
    {/* 받을 돈 is reloaded when the owner goes back home */}
    {moveOpen && <MoveCreditDialog onClose={() => setMoveOpen(false)} onMoved={() => undefined} />}
    <ContactSheet open={contact} appVersion="에코빌 v0.1" onClose={() => setContact(false)} />
    {editing && <BusinessInfoDialog initialName={business?.business_name ?? ""} busy={busy} error={businessError} failure={businessFailure} onSave={saveBusiness} onClose={() => { if (!busy) setEditing(false); }} />}
    <ConfirmDialog open={loggingOut} destructive title="로그아웃할까요?" message="이 기기에 임시 저장된 작성 내용은 지워져요."
      confirmLabel={busy ? "로그아웃 중…" : "로그아웃"} onConfirm={() => { if (!busy) void endSession(); }} onCancel={() => { if (!busy) setLoggingOut(false); }} />
  </>;
}
