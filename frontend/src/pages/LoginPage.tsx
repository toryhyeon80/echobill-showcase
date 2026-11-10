import { localStore } from "../utils/browserStorage";
import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { loginKakao, logout } from "../api/client";
import { signup, signupRequest, type SignupRequest } from "../api/consent";
import { useAuthStore, useCandidateStore } from "../stores/appStore";
import { describeApiFailure } from "../utils/apiError";
import { clearSessionDrafts } from "../utils/draftStorage";
import { createRequestKey } from "../utils/requestKey";
import { createLoginUrl, consumeLoginCallback } from "../utils/kakaoLogin";
import type { LoginResponse } from "../types/api";
import { LoginView } from "../ui/LoginView";
import { TermsAgreementSheet } from "../ui/TermsAgreementSheet";
import { DocumentSheet, type DocumentKind } from "../ui/DocumentSheet";
import { WelcomeView } from "../ui/WelcomeView";
import { GreetingView } from "../ui/GreetingView";

/** OAuth → existing login or explicit signup consent; only the server creates the member. */
export function LoginPage({ callback = false }: { callback?: boolean }) {
  const navigate = useNavigate();
  const started = useRef(false);
  const mounted = useRef(true);
  const pending = useRef<{ body: SignupRequest; key: string } | null>(null);
  const busyRef = useRef(false);
  const [error, setError] = useState<string | null>(null);
  const [phase, setPhase] = useState<"login" | "checking" | "signup" | "welcome" | "greeting">(callback ? "checking" : "login");
  const [doc, setDoc] = useState<DocumentKind | null>(null);
  const [busy, setBusy] = useState(false);
  const [locked, setLocked] = useState(false);
  const clientId = import.meta.env.VITE_KAKAO_REST_API_KEY;
  const goHome = useCallback(() => navigate("/record", { replace: true }), [navigate]);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);

  function adopt(session: LoginResponse, isNew: boolean) {
    localStore.removeItem("echobill_access_token");
    localStore.removeItem("echobill_user_id");
    useAuthStore.getState().setCookieSession(session.user_id, session.voice_answer_fields, session.ai_consent);
    void useCandidateStore.getState().hydrateFromStorage();
    pending.current = null;
    setError(null); setLocked(false); setPhase(isNew ? "welcome" : "greeting");
  }

  useEffect(() => {
    if (!callback || started.current) return;
    started.current = true;
    const code = consumeLoginCallback(window.location.search);
    window.history.replaceState(null, "", "/auth/kakao/callback");
    if (!code) { setError("로그인을 완료하지 못했어요. 카카오로 다시 시작해 주세요."); setPhase("login"); return; }
    void loginKakao(code).then((session) => {
      if (!mounted.current) return;
      if (session.signup_required === true) {
        clearSessionDrafts();
        useAuthStore.getState().setCookieSession(null);
        useCandidateStore.getState().setCandidate(null);
        window.dispatchEvent(new Event("echobill:session-ended"));
        setPhase("signup");
      } else adopt(session, false);
    }).catch((failure: unknown) => {
      if (mounted.current) { setError(describeApiFailure(failure).message); setPhase("login"); }
    });
  }, [callback]);

  async function agree(aiConsent: boolean) {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true); setError(null);
    try {
      const request = pending.current ?? { body: signupRequest(aiConsent), key: createRequestKey() };
      pending.current = request;
      const session = await signup(request.body, request.key);
      if (mounted.current) adopt(session, true);
    } catch (failure) {
      if (!mounted.current) return;
      const result = describeApiFailure(failure);
      if (result.kind === "auth") {
        pending.current = null; setLocked(false); setPhase("login");
        setError("가입을 확인하지 못했어요. 카카오로 다시 시작하면 이어서 진행돼요.");
      } else {
        const uncertain = ["network", "server", "external", "pending", "rate"].includes(result.kind);
        if (!uncertain) pending.current = null;
        setLocked(uncertain);
        setError(result.message);
      }
    } finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }

  async function cancelSignup() {
    if (busyRef.current) return;
    busyRef.current = true; setBusy(true);
    try {
      await logout();
      if (mounted.current) { pending.current = null; setLocked(false); setError(null); setPhase("login"); }
    } catch (failure) { if (mounted.current) setError(describeApiFailure(failure).message); }
    finally { busyRef.current = false; if (mounted.current) setBusy(false); }
  }

  function startLogin() {
    if (!clientId) return;
    const redirectUri = import.meta.env.VITE_KAKAO_REDIRECT_URI || `${window.location.origin}/auth/kakao/callback`;
    try { window.location.assign(createLoginUrl(clientId, redirectUri)); }
    catch { setError("이 브라우저에서 로그인을 시작하지 못했어요. 카카오톡을 업데이트하거나 다른 브라우저에서 열어 주세요."); }
  }
  if (phase === "welcome") return <WelcomeView onStart={goHome} />;
  if (phase === "greeting") return <GreetingView onDone={goHome} />;
  return <>
    <LoginView checking={phase === "checking"} error={phase === "login" ? error : null} disabled={!clientId || busy}
      onKakaoLogin={startLogin} onOpenPrivacy={() => setDoc("privacy")} />
    <TermsAgreementSheet open={phase === "signup"} submitting={busy} locked={locked} error={error}
      onAgree={({ aiConsent }) => void agree(aiConsent)} onClose={() => void cancelSignup()} onOpenDocument={setDoc} />
    <DocumentSheet doc={doc} onClose={() => setDoc(null)} />
  </>;
}
