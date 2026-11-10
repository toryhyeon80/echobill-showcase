import { DesignShell } from "../src/components/DesignShell";
import { beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import axios, { AxiosError } from "axios";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import { api, parseUtterance, parseCorrectedCandidate, postOnce, recordPayment, sendFeedback } from "../src/api/client";
import { describeApiFailure, errorNoticeKind } from "../src/utils/apiError";
import { RequestError } from "../src/components/RequestError";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { createCandidateDraft, saveCandidateDraft, restoreCandidateDraft, updateDraftItem, draftToCandidateFields } from "../src/utils/candidateDraft";
import { readDraft, writeDraft, clearSessionDrafts, readUtteranceDraft, saveUtteranceDraft } from "../src/utils/draftStorage";
import { createLoginUrl, consumeLoginCallback } from "../src/utils/kakaoLogin";
import { parseRecording } from "../src/api/client";
import { kstRegistrationLabel } from "../src/utils/useLedgerData";
import type { LedgerList } from "../src/types/api";
import { signup, signupRequest } from "../src/api/consent";
import { TermsAgreementSheet } from "../src/ui/TermsAgreementSheet";
import { POLICY_META } from "../src/ui/privacyPolicy";
import { TERMS_META } from "../src/ui/termsOfService";
import { createRequestKey } from "../src/utils/requestKey";

function storage() {
  const values = new Map<string, string>();
  return { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key), clear: () => values.clear(), entries: () => [...values],
    get length() { return values.size; }, key: (index: number) => [...values.keys()][index] ?? null };
}
const retryStorage = storage();
Object.assign(globalThis, { localStorage: storage(), sessionStorage: retryStorage, window: new EventTarget() });
beforeEach(() => { localStorage.clear(); sessionStorage.clear(); useAuthStore.getState().setCookieSession(null); });

const snapshot = { entry_id: "draft-entry", utterance_text: "테스트", counterparty: "테스트상사",
  items: [{ name: "배추", qty: 2, unit: null, unit_price: null, amount: 1000 }], total_amount: 1000, credit: true };

test("signup sends displayed document versions and optional consent only on explicit submission", async () => {
  const previous = api.defaults.adapter;
  try {
    let calls = 0;
    api.defaults.adapter = async (config) => {
      calls++;
      assert.equal(config.url, "/api/v1/auth/signup");
      assert.equal(config.headers["X-Idempotency-Key"], "signup-test-key");
      assert.deepEqual(JSON.parse(config.data), { terms_version: TERMS_META.version, privacy_version: POLICY_META.version,
        age_over_14: true, ai_consent: false });
      return { config, data: { user_id: "new-owner", ai_consent: false }, status: 200, statusText: "OK", headers: {} };
    };
    const html = renderToStaticMarkup(<TermsAgreementSheet open onClose={() => {}} onOpenDocument={() => {}} onAgree={() => {}} />);
    assert.equal(calls, 0);
    assert.match(html, /disabled=""[^>]*>동의하고 시작하기/);
    assert.match(html, /aria-checked="false"/);
    await signup(signupRequest(false), "signup-test-key");
    assert.equal(calls, 1);
    assert.equal(sessionStorage.length, 0);
  } finally { api.defaults.adapter = previous; }
});

test("signup API propagates a lost response without reading an unrelated login session", async () => {
  const previous = api.defaults.adapter;
  try {
    const urls: string[] = [];
    api.defaults.adapter = async (config) => {
      urls.push(config.url!);
      if (config.url?.endsWith("/signup")) throw new AxiosError("lost response", undefined, config);
      return { config, data: { user_id: "new-owner", ai_consent: false }, status: 200, statusText: "OK", headers: {} };
    };
    await assert.rejects(signup(signupRequest(false), "same-key"));
    assert.deepEqual(urls, ["/api/v1/auth/signup"]);
  } finally { api.defaults.adapter = previous; }
});

test("expired signup with no login stays an auth error and never sends another signup", async () => {
  const previous = api.defaults.adapter;
  try {
    let calls = 0;
    api.defaults.adapter = async (config) => {
      calls++;
      throw new AxiosError("expired", undefined, config, undefined,
        { config, data: { error_code: "SIGNUP_EXPIRED" }, status: 401, statusText: "Unauthorized", headers: {} });
    };
    await assert.rejects(signup(signupRequest(false), "expired-key"), (error: unknown) => describeApiFailure(error).kind === "auth");
    assert.equal(calls, 1);
  } finally { api.defaults.adapter = previous; }
});

test("signup API preserves caller-supplied key and consent on replay", async () => {
  const previous = api.defaults.adapter;
  try {
    const requests: Array<{ body: string; key: unknown }> = [];
    api.defaults.adapter = async (config) => {
      if (config.url?.endsWith("/signup")) {
        requests.push({ body: config.data, key: config.headers["X-Idempotency-Key"] });
        if (requests.length === 1) throw new AxiosError("lost", undefined, config);
        return { config, data: { user_id: "new-owner" }, status: 200, statusText: "OK", headers: {} };
      }
      throw new AxiosError("not signed up", undefined, config, undefined,
        { config, data: {}, status: 401, statusText: "Unauthorized", headers: {} });
    };
    const body = signupRequest(false);
    await assert.rejects(signup(body, "original-key"));
    await signup(body, "original-key");
    assert.deepEqual(requests[0], requests[1]);
  } finally { api.defaults.adapter = previous; }
});

test("a late session read cannot undo an acknowledged consent change", async () => {
  const previous = api.defaults.adapter;
  try {
    useAuthStore.getState().setCookieSession("owner", [], false);
    let resolveRead!: () => void;
    api.defaults.adapter = (config) => new Promise((resolve) => {
      resolveRead = () => resolve({ config, data: { user_id: "owner", ai_consent: false }, status: 200, statusText: "OK", headers: {} });
    });
    const read = useAuthStore.getState().hydrateFromStorage();
    await new Promise((resolve) => setTimeout(resolve, 0));
    useAuthStore.getState().setAiConsent(true);
    resolveRead(); await read;
    assert.equal(useAuthStore.getState().aiConsent, true);
  } finally { api.defaults.adapter = previous; }
});

test("secure request keys work when the webview lacks randomUUID", () => {
  const source = { getRandomValues: crypto.getRandomValues.bind(crypto) };
  const first = createRequestKey(source), second = createRequestKey(source);
  assert.match(first, /^[a-f0-9]{8}-[a-f0-9]{4}-4[a-f0-9]{3}-[89ab][a-f0-9]{3}-[a-f0-9]{12}$/);
  assert.notEqual(first, second);
});

test("consent capability clears on legacy session and logout", async () => {
  const previous = api.defaults.adapter;
  try {
    useAuthStore.getState().setCookieSession("owner", [], true);
    api.defaults.adapter = async (config) => ({ config, data: { user_id: "owner", ai_consent: false }, status: 200, statusText: "OK", headers: {} });
    await useAuthStore.getState().hydrateFromStorage(); assert.equal(useAuthStore.getState().aiConsent, false);
    api.defaults.adapter = async (config) => ({ config, data: { user_id: "owner" }, status: 200, statusText: "OK", headers: {} });
    await useAuthStore.getState().hydrateFromStorage(); assert.equal(useAuthStore.getState().aiConsent, null);
    useAuthStore.getState().setCookieSession("owner", [], true);
    useAuthStore.getState().setCookieSession(null); assert.equal(useAuthStore.getState().aiConsent, null);
  } finally { api.defaults.adapter = previous; }
});

test("a late 401 cannot expire a newly logged-in session", async () => {
  let release!: () => void;
  let sent!: () => void;
  const pendingResponse = new Promise<void>((resolve) => { release = resolve; });
  const dispatched = new Promise<void>((resolve) => { sent = resolve; });
  let expired = 0;
  const count = () => { expired += 1; };
  window.addEventListener("echobill:session-expired", count);
  api.defaults.adapter = async (config) => {
    sent(); await pendingResponse;
    throw new AxiosError("expired", "ERR_BAD_REQUEST", config, undefined, { status: 401, statusText: "Unauthorized", data: {}, headers: {}, config });
  };
  const request = api.get("/api/v1/ledger");
  await dispatched;
  useAuthStore.getState().setCookieSession("new-login");
  release(); await assert.rejects(request);
  assert.equal(expired, 0);
  await assert.rejects(api.get("/api/v1/ledger"));
  assert.equal(expired, 1);
  window.removeEventListener("echobill:session-expired", count);
});

test("private routes show only session checking before restoration, even with a cached identity", () => {
  for (const userId of [null, "cached-user"]) {
    useAuthStore.setState({ userId, ready: false, sessionError: null });
    const markup = renderToStaticMarkup(<StaticRouter location="/record"><DesignShell /></StaticRouter>);
    assert.match(markup, /로그인을 확인하고 있어요/);
    assert.doesNotMatch(markup, /눌러서 말하기|후보 만들기|받을 돈/);
  }
  useAuthStore.setState({ userId: null, ready: false });
});
