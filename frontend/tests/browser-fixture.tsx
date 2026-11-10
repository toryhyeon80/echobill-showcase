import { sessionStore } from "../src/utils/browserStorage";
import React from "react";
import { createRoot } from "react-dom/client";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AxiosError } from "axios";
import App from "../src/App";
import { api } from "../src/api/client";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import "../src/styles/index.css";
import { clearSessionDrafts } from "../src/utils/draftStorage";
import { WelcomeView } from "../src/ui/WelcomeView";

// This fixture is a Vite development page, never a production entry point.
const params = new URLSearchParams(location.search);
if (params.has("reset")) clearSessionDrafts();
const signupMode = params.get("signupMode");
if (signupMode) {
  // Synthetic OAuth state: no Kakao or other external service is contacted.
  sessionStore.setItem("echobill_oauth_state", "synthetic-state");
  const query = new URLSearchParams(location.search);
  query.set("state", "synthetic-state"); query.set("code", "synthetic-code");
  history.replaceState(null, "", `${location.pathname}?${query}`);
}
// Optional synthetic recording exercises the real voice failure UI without microphone access.
if (params.has("audioError") || params.has("audioMode") || params.has("micMode")) {
  class SyntheticRecorder {
    static isTypeSupported() { return true; }
    state = "inactive";
    mimeType = "audio/webm";
    ondataavailable: ((event: { data: Blob }) => void) | null = null;
    onstop: (() => void) | null = null;
    start() { this.state = "recording"; }
    stop() {
      this.state = "inactive";
      const content = params.get("audioMode") === "empty" ? "" : params.get("audioMode") === "oversize" ? new Uint8Array(5_000_001) : "synthetic fixture bytes";
      this.ondataavailable?.({ data: new Blob([content], { type: this.mimeType }) });
      this.onstop?.();
    }
  }
  Object.defineProperty(navigator, "mediaDevices", { configurable: true, value: {
    getUserMedia: async () => {
      if (params.get("micMode") === "pending") return new Promise(() => {});
      if (params.get("micMode") === "denied") throw new DOMException("synthetic denial", "NotAllowedError");
      if (params.get("micMode") === "permission-hide") {
        Object.defineProperty(document, "visibilityState", { configurable: true, value: "hidden" });
        document.dispatchEvent(new Event("visibilitychange"));
        await new Promise((resolve) => setTimeout(resolve, 100));
        Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
        document.dispatchEvent(new Event("visibilitychange"));
      }
      if (params.get("audioMode") === "denied") throw new DOMException("Synthetic denial", "NotAllowedError");
      return { getTracks: () => [{ stop() { /* No actual microphone. */ } }] };
    },
  } });
  Object.defineProperty(window, "MediaRecorder", { configurable: true, value: SyntheticRecorder });
}
const key = "echobill_browser_fixture";
const initial = { auth: true, candidate: { entry_id: "fixture-entry", utterance_text: "시연상사 배추 2박스 2만원 외상", status: "candidate",
  counterparty: "시연상사", items: [{ name: "배추", qty: 2, unit: "박스", unit_price: 10000, amount: 20000 }], total_amount: 20000,
  credit: params.get("credit") === "null" ? null : true, created_at: "2026-10-06T00:00:00Z" }, payments: [] as Array<{ amount: number; created_at: string; kst_date: string }>,
  replays: {} as Record<string, unknown>, lost: false };
const fixture: typeof initial = params.has("reset") || !sessionStore.getItem(key) ? initial : JSON.parse(sessionStore.getItem(key)!);
if (["tax", "ledger"].includes(params.get("page") ?? "")) fixture.candidate.status = "confirmed";
if (["debt", "cash"].includes(params.get("homeState") ?? "")) fixture.candidate.status = "confirmed";
if (params.get("homeState") === "cash") fixture.candidate.credit = false;
if (signupMode) fixture.auth = false;
if (params.get("auth") === "none") fixture.auth = false;
let aiConsent = false;
let signupLost = false;
let consentLost = false;
let taxRegistered = params.get("businessMode") === "registered" || params.has("businessRegistered");
let businessName = taxRegistered ? "시연상사" : null as string | null;
let taxState: "none" | "requested" | "issued" | "failed" = "none";
let taxLost = false;
const taxView = () => ({ entry_id: "fixture-entry", counterparty: "시연상사", total_amount: 20000, state: taxState, state_label: taxState, is_mock: true,
  fail_reason: taxState === "failed" ? "가상 모의 발행 실패" : null, notice: "모의 발행입니다. 실제 국세청 전송이 아닙니다." });
const save = () => sessionStore.setItem(key, JSON.stringify(fixture));
save();
useCandidateStore.getState().setCandidate(fixture.candidate);
if (params.has("noDraft")) useCandidateStore.getState().setCandidate(null);
if (params.get("storageBlocked") === "true") {
  for (const name of ["localStorage", "sessionStorage"])
    Object.defineProperty(window, name, { configurable: true, get: () => { throw new DOMException("synthetic storage restriction", "SecurityError"); } });
}

const injectedFailures = new Set<string>();
api.defaults.adapter = async (config) => {
  const result = (data: unknown, status = 200) => ({ data, status, statusText: "OK", headers: {}, config });
  const reject = (status: number, error_code: string) => { throw new AxiosError("fixture response", undefined, config, undefined, result({ error_code }, status)); };
  const url = config.url!;
  if (url.endsWith("/auth/me")) {
    if (params.has("authDelay")) await new Promise((resolve) => setTimeout(resolve, Number(params.get("authDelay"))));
    if (params.get("auth") === "outage") reject(500, "INTERNAL_ERROR");
  }
  if (params.get("ledgerFailure") === "true" && url.endsWith("/ledger")) reject(500, "INTERNAL_ERROR");
  if (params.has("delayMs") && (url.endsWith("/ledger") || url.endsWith("/dashboard/unpaid")))
    await new Promise((resolve) => setTimeout(resolve, Number(params.get("delayMs")) || 0));
  if (params.get("noticeAt") && url.endsWith(`/${params.get("noticeAt")}`) && !injectedFailures.has(url)) {
    injectedFailures.add(url);
    const response = result({ error_code: params.get("noticeCode") ?? "RATE_LIMITED" }, Number(params.get("noticeStatus") ?? 429));
    throw new AxiosError("synthetic notice", undefined, config, undefined, { ...response, headers: { "retry-after": "2" } });
  }
  const body = typeof config.data === "string" ? JSON.parse(config.data) : config.data;
  if (url.endsWith("/auth/logout")) { fixture.auth = false; save(); return result(null, 204); }
  if (url.endsWith("/auth/me")) { if (!fixture.auth) reject(401, "UNAUTHORIZED"); return result({ user_id: "fixture-user", expires_in_hours: 24, voice_answer_fields: params.has("voiceFields") ? params.get("voiceFields")!.split(",").filter(Boolean) : ["qty", "total"],
    ...(params.has("consentMode") || signupMode ? { ai_consent: aiConsent } : {}) }); }
  if (url.endsWith("/auth/kakao")) return result({ signup_required: true, expires_in_minutes: 10 });
  if (url.endsWith("/auth/signup")) {
    if (signupMode === "lost" && signupLost) reject(401, "SIGNUP_EXPIRED");
    if (signupMode === "expired") reject(401, "SIGNUP_EXPIRED");
    if (!body.age_over_14 || !body.terms_version || !body.privacy_version) reject(400, "CONSENT_REQUIRED");
    if (signupMode === "retry" && !signupLost) { signupLost = true; throw new AxiosError("synthetic signup response lost before commit", undefined, config); }
    fixture.auth = true; aiConsent = body.ai_consent;
    if (signupMode === "lost" && !signupLost) { signupLost = true; throw new AxiosError("synthetic signup response lost after commit", undefined, config); }
    return result({ user_id: "fixture-user", expires_in_hours: 24, ai_consent: aiConsent, voice_answer_fields: [] });
  }
  if (url.endsWith("/me/consent")) {
    aiConsent = body.ai_consent;
    if (params.get("consentMode") === "lost" && !consentLost) { consentLost = true; throw new AxiosError("synthetic consent response lost", undefined, config); }
    return result({ ai_consent: aiConsent, ai_consent_at: "2026-10-12T00:00:00Z" });
  }
  if (url.endsWith("/voice-answer")) {
    const field = (body as FormData).get("field");
    const mode = params.get("answerMode");
    if (mode === "disabled") reject(400, "VOICE_ANSWER_DISABLED");
    if (mode === "timeout") reject(504, "STT_TIMEOUT");
    if (mode === "rate") reject(429, "RATE_LIMIT_EXCEEDED");
    const values: Record<string, string | number | boolean> = { credit: false, qty: 2, total: 20000, counterparty: "시연상사" };
    return result({ field, status: mode === "failed" ? "failed" : "ok", value: mode === "failed" ? null : values[String(field)],
      unit: field === "qty" && mode !== "number-only" ? "박스" : null, utterance_text: mode === "failed" ? "잘 모르겠어요" : String(values[String(field)]) });
  }
  if (url.endsWith("/tax/business")) {
    if (config.method === "get" && params.get("businessMode") === "load-failed" && !injectedFailures.has(url)) {
      injectedFailures.add(url); throw new AxiosError("synthetic business load failure", undefined, config);
    }
    if (config.method === "post") {
      if (params.get("businessMode") === "load-failed") await new Promise((resolve) => setTimeout(resolve, 1000));
      taxRegistered = true; businessName = body.business_name;
    }
    return result({ registered: taxRegistered, business_name: businessName, business_number_masked: taxRegistered ? "***-**-****1" : null });
  }
  if (url.endsWith("/tax/issue")) {
    if (!taxRegistered) reject(400, "BUSINESS_INFO_REQUIRED");
    taxState = params.get("taxMode") === "failed" ? "failed" : "requested";
    if (params.get("taxMode") === "lost" && !taxLost) { taxLost = true; throw new AxiosError("synthetic lost invoice", undefined, config); }
    return result(taxView());
  }
  if (url.endsWith("/tax/fixture-entry")) { if (taxState === "requested") taxState = "issued"; return result(taxView()); }
  if (url.endsWith("/tax")) return result({ invoices: taxState === "none" ? [] : [taxView()] });
  if ((url.endsWith("/parse") || url.endsWith("/parse-audio")) && params.has("nonSale")) {
    const redirect = params.get("nonSale");
    const messages: Record<string, string> = {
      payment: "입금은 받을 돈 탭에서 [입금 등록]으로 적어 주세요.",
      edit: "이미 적은 장부는 장부 탭에서 그 기록을 눌러 고쳐 주세요.",
      inquiry: "남은 외상은 받을 돈 탭에서 볼 수 있어요.",
      moved_credit: "그동안 쌓인 외상은 받을 돈 탭의 [외상 옮기기]로 한 번에 넣어 주세요.",
    };
    throw new AxiosError("synthetic non-sale", undefined, config, undefined, result({
      error_code: "NOT_A_SALE", redirect, message: messages[redirect ?? ""] ?? "장부에서 확인해 주세요.",
      problem_fields: [], partial_candidate: null,
      ...(url.endsWith("/parse-audio") ? { utterance_text: "김씨네 외상값 오만 원 입금 들어왔어" } : {}),
    }, 400));
  }
  if (url.endsWith("/parse-audio") && params.has("audioError")) {
    const mode = params.get("audioError");
    throw new AxiosError("synthetic voice rejection", undefined, config, undefined, result({
      error_code: mode === "missing" ? "MISSING_REQUIRED_FIELD" : mode === "unsupported" ? "UNSUPPORTED_TRANSACTION" : mode === "timeout" ? "STT_TIMEOUT" : "VALIDATION_ERROR",
      message: mode === "timeout" ? "텍스트로 입력해 주세요." : "거래 금액을 확인해 주세요.",
      ...(mode === "transcript" || mode === "unsupported" || mode === "missing" ? { utterance_text: "시연상사 배추 두 박스 외상" } : {}),
      ...(mode === "missing" ? { problem_fields: ["total_amount"], partial_candidate: {
        counterparty: "시연상사", items: [{ name: "배추", qty: 2, unit: "박스", unit_price: null, amount: null }], total_amount: null, credit: true,
      } } : {}),
    }, mode === "timeout" ? 504 : 400));
  }
  if (url.endsWith("/parse") && params.get("parseError") === "unsupported") reject(400, "UNSUPPORTED_TRANSACTION");
  if ((url.endsWith("/parse") || url.endsWith("/parse-audio")) && ["missing", "credit"].includes(params.get("parseError") ?? "") && !body?.candidate) {
    const creditOnly = params.get("parseError") === "credit";
    throw new AxiosError("synthetic missing fields", undefined, config, undefined, result({
      error_code: "MISSING_REQUIRED_FIELD", message: "빠진 정보를 채워 주세요.",
      problem_fields: creditOnly ? ["credit"] : ["credit", "items[0].qty", "total_amount", "counterparty"],
      partial_candidate: creditOnly ? { counterparty: initial.candidate.counterparty, items: initial.candidate.items, total_amount: initial.candidate.total_amount, credit: null }
        : { counterparty: null, items: [{ name: "배추", qty: null, unit: "박스", unit_price: null, amount: null }], total_amount: null, credit: null },
      ...(url.endsWith("/parse-audio") ? { utterance_text: "배추 박스" } : {}),
    }, 400));
  }
  if (url.endsWith("/feedback")) {
    if (body.action === "reject") fixture.candidate.status = "rejected";
    else {
      if (params.get("expired") === "true") reject(409, "CANDIDATE_EXPIRED");
      if (body.corrected) Object.assign(fixture.candidate, body.corrected);
      fixture.candidate.status = "confirmed";
    }
    save(); return result(fixture.candidate);
  }
  if (url.endsWith("/parse-audio") || url.endsWith("/parse")) {
    if (body?.candidate) Object.assign(fixture.candidate, body.candidate);
    if (typeof body?.utterance_text === "string") fixture.candidate.utterance_text = body.utterance_text;
    fixture.candidate.status = "candidate"; save(); return result({ ...fixture.candidate, parser_type: body?.candidate ? "manual" : "rule", is_fallback_used: !body?.candidate });
  }
  if (url.endsWith("/payments")) {
    const id = String(config.headers["X-Idempotency-Key"]);
    if (fixture.replays[id]) return result(fixture.replays[id]);
    fixture.payments.push({ amount: body.amount, created_at: new Date().toISOString(), kst_date: "2026-10-06" });
    fixture.replays[id] = {}; save();
    if (!fixture.lost) { fixture.lost = true; save(); throw new AxiosError("fixture lost payment response", undefined, config); }
    return result({});
  }
  const credit = fixture.candidate.status === "confirmed" && fixture.candidate.credit ? fixture.candidate.total_amount : 0;
  const paid = fixture.payments.reduce((sum, payment) => sum + payment.amount, 0);
  const balance = { counterparty: fixture.candidate.counterparty, credit_total: credit, paid_total: paid, unpaid: credit - paid,
    oldest_days: credit > paid ? 32 : null, owed_count: credit > paid ? 1 : 0 };
  if (url.includes("/counterparty/")) return result({ ...balance, entries: [{ entry_id: fixture.candidate.entry_id, kst_date: "2026-10-06", total_amount: credit, allocated: paid, remaining: credit - paid }], payments: fixture.payments });
  if (url.endsWith("/dashboard/unpaid")) return result({ total_unpaid: credit - paid, counterparties: credit - paid > 0 ? [balance] : [],
    has_confirmed_entries: fixture.candidate.status === "confirmed" });
  if (url.endsWith("/ledger")) {
    const recent = params.has("recentNames") ? ["가나상회", "솔바람식자재", "푸른마트"].map((counterparty, index) => ({
      ...initial.candidate, entry_id: `recent-${index}`, status: "confirmed", counterparty,
      created_at: `2026-10-${String(index + 1).padStart(2, "0")}T00:00:00Z`, kst_date: `2026-10-0${index + 1}`,
    })) : [];
    return result({ month: "2026-10", entries: [...recent, ...(fixture.candidate.status === "confirmed"
      ? [{ ...fixture.candidate, kst_date: "2026-10-06" }] : [])], daily_credit: [{ date: "2026-10-06", credit_amount: credit }] });
  }
  if (url.endsWith("/fixture-entry")) return result(fixture.candidate);
  return reject(404, "NOT_FOUND");
};

const router = createMemoryRouter([{ path: "*", element: <>{!params.has("compare") && <p className="notice">로컬 회귀 점검 · 가상 데이터 · 실제 로그인·입금·음성 호출 없음</p>}
  <>{params.has("sessionExpiry") && <button className="fixed top-0 left-0 z-[100] bg-white p-2" onClick={() => { fixture.auth = false; useAuthStore.getState().setCookieSession(null); }}>테스트 세션 만료</button>}</>{!params.has("compare") && <button onClick={() => void router.navigate(-1)}>테스트 뒤로가기</button>}{params.get("page") === "welcome" ? <WelcomeView onStart={() => void router.navigate("/record")} /> : <App />}</> }],
  { initialEntries: ["/ledger", signupMode ? "/auth/kakao/callback" : params.get("page") === "ledger" ? "/ledger" : params.get("page") === "my" ? "/my-page" : params.get("page") === "tax" ? "/tax-invoice" : params.get("page") === "record" ? "/record" : "/record/confirm"] });
createRoot(document.getElementById("root")!).render(<React.StrictMode><RouterProvider router={router} /></React.StrictMode>);
