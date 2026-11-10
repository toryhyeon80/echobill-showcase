import { localStore } from "../utils/browserStorage";
import { create } from "zustand";
import type { CandidateSnapshot } from "../types/api";
import { advanceSessionEpoch, currentSessionEpoch, getEntry, getSession } from "../api/client";
import { describeApiFailure, type ApiFailure } from "../utils/apiError";
import { clearSessionDrafts } from "../utils/draftStorage";

const CANDIDATE_KEY = "echobill_last_candidate";
let candidateRevision = 0;
let authRevision = 0;

/** A response may restore its candidate only while no newer candidate work has superseded it. */
export function guardCandidateResponse(): () => boolean {
  const revision = candidateRevision;
  return () => revision === candidateRevision;
}

function adoptDraftOwner(userId: string): void {
  const previous = localStore.getItem("echobill_draft_owner");
  if (previous && previous !== userId) {
    clearSessionDrafts();
    window.dispatchEvent(new Event("echobill:session-ended"));
    useCandidateStore.getState().setCandidate(null);
  }
  localStore.setItem("echobill_draft_owner", userId);
}

/** Reject candidates cached before DL-01 renamed their API fields. */
function isCurrentCandidate(value: unknown): value is CandidateSnapshot {
  return !!value && typeof value === "object" &&
    "entry_id" in value && typeof value.entry_id === "string" &&
    "utterance_text" in value && typeof value.utterance_text === "string" &&
    "items" in value && Array.isArray(value.items);
}

interface AuthState {
  aiConsent: boolean | null;
  setAiConsent: (value: boolean) => void;
  voiceAnswerFields: string[];
  ready: boolean;
  userId: string | null;
  accessToken: string | null;
  setSession: (userId: string, accessToken: string) => void;
  hydrateFromStorage: () => Promise<void>;
  setCookieSession: (userId: string | null, voiceAnswerFields?: string[], aiConsent?: boolean | null) => void;
  sessionError: ApiFailure | null;
}

/** Auth session for JWT ownership wiring. */
export const useAuthStore = create<AuthState>((set, get) => ({
  aiConsent: null,
  setAiConsent: (aiConsent) => { authRevision += 1; set({ aiConsent }); },
  voiceAnswerFields: [],
  ready: false,
  userId: null,
  accessToken: null,
  sessionError: null,
  setCookieSession: (userId, voiceAnswerFields = [], aiConsent = null) => {
    authRevision += 1;
    advanceSessionEpoch();
    if (userId) adoptDraftOwner(userId);
    set({ userId, aiConsent: userId ? aiConsent : null, voiceAnswerFields: userId ? voiceAnswerFields : [], accessToken: null, ready: true, sessionError: null });
  },
  setSession: (userId, accessToken) => {
    if (!import.meta.env.DEV) return;
    authRevision += 1;
    advanceSessionEpoch();
    adoptDraftOwner(userId);
    localStore.setItem("echobill_user_id", userId);
    localStore.setItem("echobill_access_token", accessToken);
    set({ userId, aiConsent: null, voiceAnswerFields: [], accessToken, ready: true, sessionError: null });
  },
  hydrateFromStorage: async () => {
    const revision = ++authRevision;
    try {
      const session = await getSession();
      if (revision !== authRevision) return;
      // Initial cookie discovery keeps startup reads valid; replacing a known owner does not.
      if (get().userId && get().userId !== session.user_id) advanceSessionEpoch();
      adoptDraftOwner(session.user_id);
      set({ userId: session.user_id, aiConsent: typeof session.ai_consent === "boolean" ? session.ai_consent : null, voiceAnswerFields: session.voice_answer_fields ?? [], accessToken: import.meta.env.DEV ? localStore.getItem("echobill_access_token") : null, ready: true, sessionError: null });
    } catch (error) {
      if (revision !== authRevision) return;
      const failure = describeApiFailure(error);
      if (failure.kind === "auth") {
        if (get().userId) advanceSessionEpoch();
        set({ userId: null, aiConsent: null, voiceAnswerFields: [], accessToken: null, ready: true, sessionError: null });
      } else set({ ready: true, sessionError: failure });
    }
  },
}));

interface CandidateState {
  candidate: CandidateSnapshot | null;
  hydrated: boolean;
  restoreError: string | null;
  setCandidate: (candidate: CandidateSnapshot | null) => void;
  hydrateFromStorage: () => void;
}

/**
 * Last parse candidate — session restore for Kakao swipe-close.
 * Confirmed ledger writes happen only after explicit feedback (not here).
 */
export const useCandidateStore = create<CandidateState>((set) => ({
  candidate: null,
  hydrated: false,
  restoreError: null,
  setCandidate: (candidate) => {
    candidateRevision += 1;
    if (candidate) {
      localStore.setItem(CANDIDATE_KEY, JSON.stringify({ entry_id: candidate.entry_id }));
    } else {
      localStore.removeItem(CANDIDATE_KEY);
    }
    set({ candidate, hydrated: true, restoreError: null });
  },
  hydrateFromStorage: async () => {
    const revision = ++candidateRevision;
    const epoch = currentSessionEpoch();
    const isCurrent = () => revision === candidateRevision && epoch === currentSessionEpoch();
    const raw = localStore.getItem(CANDIDATE_KEY);
    if (!raw) {
      set({ candidate: null, hydrated: true });
      return;
    }
    try {
      const parsed: unknown = JSON.parse(raw);
      if (!parsed || typeof parsed !== "object" || !("entry_id" in parsed) || typeof parsed.entry_id !== "string") {
        localStore.removeItem(CANDIDATE_KEY);
        set({ candidate: null, hydrated: true });
        return;
      }
      const entry = await getEntry(parsed.entry_id);
      if (!isCurrent()) return;
      if (entry.status !== "candidate") {
        localStore.removeItem(CANDIDATE_KEY);
        localStore.removeItem("echobill_candidate_draft");
        set({ candidate: null, hydrated: true });
        return;
      }
      const candidate = { ...entry, utterance_text: entry.utterance_text ?? "", counterparty: entry.counterparty ?? null,
        items: (entry.items ?? []).map((item) => ({ name: item.name, qty: item.qty ?? null, unit: item.unit ?? null, unit_price: item.unit_price ?? null, amount: item.amount ?? null })),
        total_amount: entry.total_amount ?? null, credit: entry.credit ?? null };
      if (!isCurrentCandidate(candidate)) throw new Error("Invalid candidate response");
      localStore.setItem(CANDIDATE_KEY, JSON.stringify({ entry_id: entry.entry_id }));
      set({ candidate, hydrated: true, restoreError: null });
    } catch (error) {
      if (!isCurrent()) return;
      const failure = describeApiFailure(error);
      if (failure.kind === "missing") { localStore.removeItem(CANDIDATE_KEY); localStore.removeItem("echobill_candidate_draft"); }
      set({ candidate: null, hydrated: true, restoreError: failure.message });
    }
  },
}));

interface ToastState {
  message: string | null;
  show: (message: string) => void;
  clear: () => void;
}

/**
 * Restore authentication and the saved candidate on initial entry or retry.
 * `onAuthenticated` runs once the session is confirmed (e.g. adopt startup reads); returns false when signed out or failed.
 */
export async function restoreAppSession(onAuthenticated?: (userId: string) => void): Promise<boolean> {
  await useAuthStore.getState().hydrateFromStorage();
  const { userId, sessionError } = useAuthStore.getState();
  if (!userId || sessionError) return false;
  onAuthenticated?.(userId);
  await useCandidateStore.getState().hydrateFromStorage();
  return true;
}

export const useToastStore = create<ToastState>((set) => ({
  message: null,
  show: (message) => set({ message }),
  clear: () => set({ message: null }),
}));
