import { localStore, sessionStore } from "../utils/browserStorage";
import axios, { type AxiosRequestConfig } from "axios";
import { createRequestKey } from "../utils/requestKey";
import type { ApiParseCandidate, CandidateFields, ParseCandidate, ParseRequest, TokenResponse } from "../types/api";
import type { LedgerEntry, LedgerList, MovedCreditEntry, UnpaidList, CounterpartyDetail, FeedbackRequest, LoginResponse, KakaoLoginResponse } from "../types/api";

/** Internal request metadata; never sent as an HTTP header or API field. */
declare module "axios" {
  interface AxiosRequestConfig<D> { echobillSessionEpoch?: number }
}

const baseURL = import.meta.env.VITE_API_BASE_URL ?? "";
type StartupReadConfig = AxiosRequestConfig & { startupRead?: boolean };
let sessionEpoch = 0;
const requestEpochs = new WeakMap<object, number>();
export function advanceSessionEpoch(): void {
  sessionEpoch += 1;
  api.defaults.echobillSessionEpoch = sessionEpoch;
  window.dispatchEvent(new Event("echobill:session-changed"));
}

/** Bind asynchronous request preparation to the session that started it. */
export function sessionRequestConfig(): AxiosRequestConfig {
  return { echobillSessionEpoch: sessionEpoch };
}

function assertCurrentSession(epoch: number | undefined): void {
  if (epoch !== sessionEpoch) throw new axios.CanceledError("Session changed before request dispatch");
}

/** Identify the cookie-session generation without exposing credentials. */
export function currentSessionEpoch(): number { return sessionEpoch; }

/** Axios instance — attaches Bearer JWT when present. */
export const api = axios.create({
  baseURL,
  withCredentials: true,
  headers: { "Content-Type": "application/json" },
  timeout: 15_000,
  echobillSessionEpoch: sessionEpoch,
});

api.interceptors.response.use((response) => response, (error: unknown) => {
  if (axios.isAxiosError(error) && error.response?.status === 401 && error.config &&
      requestEpochs.get(error.config) === sessionEpoch && !error.config.url?.startsWith("/api/v1/auth/") &&
      error.config.url !== "/api/v1/voice/speak" &&
      !(error.config as typeof error.config & { startupRead?: boolean }).startupRead)
    window.dispatchEvent(new Event("echobill:session-expired"));
  return Promise.reject(error);
});
api.interceptors.request.use((config) => {
  assertCurrentSession(config.echobillSessionEpoch);
  requestEpochs.set(config, sessionEpoch);
  if (!import.meta.env.DEV) return config;
  const token = localStore.getItem("echobill_access_token");
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});


/** Dev stub: mint JWT without Kakao (D07 later). */
export async function fetchDevToken(userId = "dev-user-1"): Promise<TokenResponse> {
  if (!import.meta.env.DEV) throw new Error("Dev token is unavailable in production builds");
  const { data } = await api.post<TokenResponse>("/api/v1/auth/dev-token", {
    user_id: userId,
  });
  localStore.setItem("echobill_access_token", data.access_token);
  localStore.setItem("echobill_user_id", data.user_id);
  return data;
}

/** Parse utterance → candidate only (never confirmed). */
export async function parseUtterance(utteranceText: string): Promise<ParseCandidate> {
  const body: ParseRequest = { utterance_text: utteranceText };
  const data = await postOnce<ApiParseCandidate>("parse", "/api/v1/ledger/parse", body);
  return normalizeCandidate(data);
}

/** Hash retry storage identifiers without retaining customer data in their names. */
async function retryStorageKey(operation: string, body: unknown): Promise<string> {
  const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(JSON.stringify(body)));
  const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  return `echobill_retry_${operation}_${hash}`;
}

interface RetryAttempt { storageKey: string; value: string; reused?: boolean; pending?: boolean }

/** Release only this attempt, never a newer request at the same storage key. */
export function releaseRetryAttempt(attempt: RetryAttempt): void {
  if (sessionStore.getItem(attempt.storageKey) === attempt.value) {
    sessionStore.removeItem(attempt.storageKey);
    sessionStore.removeItem(`${attempt.storageKey}_pending`);
  }
}

/** Keep the same key when an identical request is retried after a lost response. */
export async function retryKey(operation: string, body: unknown, epoch = sessionEpoch): Promise<RetryAttempt> {
  const storageKey = await retryStorageKey(operation, body);
  assertCurrentSession(epoch);
  let value = sessionStore.getItem(storageKey);
  const reused = !!value;
  if (!value) {
    value = createRequestKey();
    sessionStore.setItem(storageKey, value);
  }
  return { storageKey, value, reused, pending: sessionStore.getItem(`${storageKey}_pending`) === value };
}

/** Optional OpenAPI fields are materialized to null at the boundary. */
function normalizeCandidate(data: ApiParseCandidate): ParseCandidate {
  return {
    ...data,
    counterparty: data.counterparty ?? null,
    items: (data.items ?? []).map((item) => ({
      name: item.name,
      qty: item.qty ?? null,
      unit: item.unit ?? null,
      unit_price: item.unit_price ?? null,
      amount: item.amount ?? null,
    })),
    total_amount: data.total_amount ?? null,
    credit: data.credit ?? null,
  };
}

/** Send a corrected 400 candidate through the manual validation path. */
export async function parseCorrectedCandidate(utteranceText: string, candidate: CandidateFields, repairOf?: string): Promise<ParseCandidate> {
  const body: ParseRequest = {
    utterance_text: utteranceText,
    candidate,
    ...(repairOf ? { repair_of: repairOf } : {}),
  };
  const data = await postOnce<ApiParseCandidate>("repair", "/api/v1/ledger/parse", body);
  if (data.parser_type !== "manual") {
    throw new Error("SERVER_MANUAL_PARSE_UNAVAILABLE");
  }
  return normalizeCandidate(data);
}

// Keep retry storage independent of customer names and utterances.
export async function postOnce<T>(operation: string, url: string, body: unknown, attempt?: RetryAttempt): Promise<T> {
  const request = sessionRequestConfig();
  const key = attempt ?? await retryKey(operation, body, request.echobillSessionEpoch);
  try {
    const { data } = await api.post<T>(url, body, { ...request, headers: { "X-Idempotency-Key": key.value } });
    releaseRetryAttempt(key);
    return data;
  } catch (error) {
    if (axios.isCancel(error) && !key.reused) releaseRetryAttempt(key);
    if (axios.isAxiosError(error) && error.response?.data?.error_code === "IDEMPOTENCY_IN_PROGRESS" &&
        sessionStore.getItem(key.storageKey) === key.value) sessionStore.setItem(`${key.storageKey}_pending`, key.value);
    // A definite rejection is safe to correct and resubmit. An unknown response keeps its key.
    if (axios.isAxiosError(error) && error.response && error.response.status < 500 &&
        error.response.status !== 401 && error.response.status !== 429 &&
        error.response.data?.error_code !== "IDEMPOTENCY_IN_PROGRESS") releaseRetryAttempt(key);
    throw error;
  }
}

export const getEntry = async (id: string) => (await api.get<LedgerEntry>(`/api/v1/ledger/${encodeURIComponent(id)}`)).data;
export const sendFeedback = (id: string, body: FeedbackRequest) => postOnce<LedgerEntry>(`feedback:${id}`, `/api/v1/ledger/${encodeURIComponent(id)}/feedback`, body);
export const getLedger = async (month: string, startupRead = false) => (await api.get<LedgerList>("/api/v1/ledger", { params: { month }, ...(startupRead ? { startupRead: true } : {}) })).data;
export const getUnpaid = async (startupRead = false) => (await api.get<UnpaidList>("/api/v1/dashboard/unpaid", { startupRead } as StartupReadConfig)).data;
export const getCounterparty = async (name: string) => (await api.get<CounterpartyDetail>(`/api/v1/ledger/counterparty/${encodeURIComponent(name)}`)).data;
export const recordPayment = (counterparty: string, amount: number, attempt?: RetryAttempt) => postOnce("payment", "/api/v1/payments", { counterparty, amount }, attempt);
/** 이미 있는 외상 옮기기 (PM 10/23): one counterparty total; the same input retries with the same key. */
export const moveExistingCredit = (input: { counterparty: string; amount: number; when: "this_week" | "this_month" | "last_month" | "earlier" }) =>
  postOnce<MovedCreditEntry>("moved_credit", "/api/v1/ledger/moved-credit", input);
/** Review current settlement before the user starts a separate payment. */
export async function reviewPaymentForNewInput(counterparty: string, amount: number): Promise<CounterpartyDetail> {
  const epoch = sessionEpoch;
  const storageKey = await retryStorageKey("payment", { counterparty, amount });
  assertCurrentSession(epoch);
  const previousKey = sessionStore.getItem(storageKey);
  const detail = await getCounterparty(counterparty);
  assertCurrentSession(epoch);
  // Failed reads retain the key; a later session/request must not lose its own key.
  if (previousKey) releaseRetryAttempt({ storageKey, value: previousKey });
  return detail;
}
export const getSession = async () => (await api.get<LoginResponse>("/api/v1/auth/me")).data;
export const loginKakao = async (code: string) => (await api.post<KakaoLoginResponse>("/api/v1/auth/kakao", { code })).data;
export const logout = async () => { await api.post("/api/v1/auth/logout"); };

const configuredAudioTimeout = Number(import.meta.env.VITE_AUDIO_TIMEOUT_MS);
// BE budget: STT up to 10s + LLM 5s, plus upload/network latency.
export const AUDIO_TIMEOUT_MS = Number.isFinite(configuredAudioTimeout) && configuredAudioTimeout >= 25_000 ? configuredAudioTimeout : 25_000;

/** Voice uses the server's candidate pipeline and the audio digest to replay uncertain requests. */
export async function parseRecording(blob: Blob): Promise<ParseCandidate> {
  const request = sessionRequestConfig();
  const mime = blob.type.split(";", 1)[0].trim().toLowerCase();
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  const audioHash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const key = await retryKey("parse-audio", { audio_sha256: audioHash, mime }, request.echobillSessionEpoch);
  const extension = mime.includes("mp4") ? "mp4" : mime.includes("ogg") ? "ogg" : mime.includes("wav") ? "wav" : "webm";
  const form = new FormData();
  form.append("file", blob, `recording.${extension}`);
  try {
    const { data } = await api.post<ApiParseCandidate>("/api/v1/ledger/parse-audio", form, {
      ...request,
      headers: { "Content-Type": undefined, "X-Idempotency-Key": key.value }, timeout: AUDIO_TIMEOUT_MS,
    });
    releaseRetryAttempt(key);
    return normalizeCandidate(data);
  } catch (error) {
    if (axios.isCancel(error) && !key.reused) releaseRetryAttempt(key);
    if (axios.isAxiosError(error) && error.response && error.response.status < 500 &&
        error.response.status !== 401 && error.response.status !== 429 && error.response.data?.error_code !== "IDEMPOTENCY_IN_PROGRESS")
      releaseRetryAttempt(key);
    throw error;
  }
}
