import { hasPersistentSessionValue, sessionStore } from "./browserStorage";
import { createRequestKey } from "./requestKey";

const STATE_KEY = "echobill_oauth_state";

/** Build Kakao's authorization URL; store only a one-use CSRF nonce, never a token. */
export function createLoginUrl(clientId: string, redirectUri: string): string {
  const state = createRequestKey();
  sessionStore.setItem(STATE_KEY, state);
  if (!hasPersistentSessionValue(STATE_KEY, state)) {
    sessionStore.removeItem(STATE_KEY);
    throw new Error("OAUTH_STORAGE_UNAVAILABLE");
  }
  const query = new URLSearchParams({ client_id: clientId, redirect_uri: redirectUri, response_type: "code", state });
  return `https://kauth.kakao.com/oauth/authorize?${query}`;
}

/** Consume the nonce even on rejection; repeated or ambiguous callbacks never exchange codes. */
export function consumeLoginCallback(search: string): string | null {
  const query = new URLSearchParams(search);
  const expected = sessionStore.getItem(STATE_KEY);
  sessionStore.removeItem(STATE_KEY);
  if (!expected || query.getAll("state").length !== 1 || query.getAll("code").length !== 1 ||
      query.get("state") !== expected || !query.get("code") || query.has("error")) return null;
  return query.get("code");
}
