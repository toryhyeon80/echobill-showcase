import { localStore, sessionStore } from "./browserStorage";
const DRAFT_TTL_MS = 7 * 24 * 60 * 60 * 1000;
const INPUT_DRAFT_KEY = "echobill_utterance_draft";
const sessionKeys = new Set(["echobill_utterance_draft", "echobill_candidate_draft", "echobill_repair_draft", "echobill_last_candidate", "echobill_access_token", "echobill_user_id", "echobill_oauth_state", "echobill_draft_owner"]);

/** Store the save time separately so comparison of editable fields stays stable. */
export function writeDraft(storage: Storage, key: string, value: unknown, now = Date.now()): void {
  storage.setItem(key, JSON.stringify({ saved_at: now, value }));
}

/** Reject expired, malformed and undated legacy drafts without inventing their age. */
export function readDraft(storage: Storage, key: string, now = Date.now()): unknown {
  const raw = storage.getItem(key);
  if (!raw) return null;
  try {
    const draft: unknown = JSON.parse(raw);
    if (draft && typeof draft === "object" && "saved_at" in draft && typeof draft.saved_at === "number" &&
        Number.isFinite(draft.saved_at) && draft.saved_at <= now && now - draft.saved_at < DRAFT_TTL_MS && "value" in draft) return draft.value;
  } catch { /* Invalid legacy drafts are removed below. */ }
  storage.removeItem(key);
  return null;
}

/** Move dated drafts from the old tab storage without renewing their seven-day lifetime. */
export function readUtteranceDraft(): string {
  const local = readDraft(localStore, INPUT_DRAFT_KEY);
  if (typeof local !== "string") {
    const old = readDraft(sessionStore, INPUT_DRAFT_KEY);
    const raw = sessionStore.getItem(INPUT_DRAFT_KEY);
    if (typeof old === "string" && raw) localStore.setItem(INPUT_DRAFT_KEY, raw);
  }
  sessionStore.removeItem(INPUT_DRAFT_KEY);
  const stored = readDraft(localStore, INPUT_DRAFT_KEY);
  return typeof stored === "string" ? stored : "";
}

export function saveUtteranceDraft(value: string): void {
  if (value.trim()) {
    if (readDraft(localStore, INPUT_DRAFT_KEY) !== value) writeDraft(localStore, INPUT_DRAFT_KEY, value);
  }
  else localStore.removeItem(INPUT_DRAFT_KEY);
  sessionStore.removeItem(INPUT_DRAFT_KEY);
}

/** Remove this login's transient data from both stores, leaving other settings alone. */
export function clearSessionDrafts(): void {
  for (const storage of [localStore, sessionStore]) {
    const keys = Array.from({ length: storage.length }, (_, index) => storage.key(index));
    keys.forEach((key) => { if (key && (sessionKeys.has(key) || key.startsWith("echobill_retry_"))) storage.removeItem(key); });
  }
}
