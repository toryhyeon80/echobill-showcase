import { api } from "./client";
import type { LoginResponse } from "../types/api";
import { TERMS_META } from "../ui/termsOfService";
import { POLICY_META } from "../ui/privacyPolicy";
import type { components } from "../types/generated";

/** Current DL-32 OpenAPI contracts. Cookies/tokens remain outside JavaScript. */
export type SignupRequest = components["schemas"]["SignupRequest"];

/** Record the version of the documents actually shown; optional consent defaults off. */
export function signupRequest(aiConsent: boolean): SignupRequest {
  return { terms_version: TERMS_META.version, privacy_version: POLICY_META.version,
    age_over_14: true, ai_consent: aiConsent };
}

/** Only a response authenticated by the signup cookie can establish the new account's identity. */
export async function signup(body: SignupRequest, key: string): Promise<LoginResponse> {
  return (await api.post<LoginResponse>("/api/v1/auth/signup", body, {
    headers: { "X-Idempotency-Key": key },
  })).data;
}

type ConsentResponse = components["schemas"]["ConsentResponse"];
/** Acknowledge consent on the server before changing the visible switch. */
export async function updateConsent(aiConsent: boolean, key: string): Promise<ConsentResponse> {
  return (await api.patch<ConsentResponse>("/api/v1/me/consent", { ai_consent: aiConsent }, {
    headers: { "X-Idempotency-Key": key },
  })).data;
}
