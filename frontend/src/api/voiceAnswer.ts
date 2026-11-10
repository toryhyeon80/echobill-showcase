import axios from "axios";
import { api, AUDIO_TIMEOUT_MS, retryKey, sessionRequestConfig, releaseRetryAttempt } from "./client";
import { useAuthStore } from "../stores/appStore";
import type { components } from "../types/generated";

/** DL-31 short answers are values only; they never create or confirm entries. */
export type VoiceAnswer = components["schemas"]["VoiceAnswerResponse"];
export type VoiceAnswerField = VoiceAnswer["field"];

/** Match the deployment's enabled fields; default to button input until configured. */
export function enabledVoiceFields(setting?: string): VoiceAnswerField[] {
  const fields = setting === undefined ? useAuthStore.getState().voiceAnswerFields : setting.split(",").map((part) => part.trim());
  return (["credit", "qty", "total", "counterparty"] as const).filter((field) => fields.includes(field));
}

/** Retry identical audio with the same key, including field and displayed choices. */
export async function submitVoiceAnswer(field: VoiceAnswerField, blob: Blob, choices?: readonly number[]): Promise<VoiceAnswer> {
  const request = sessionRequestConfig();
  const mime = blob.type.split(";", 1)[0].trim().toLowerCase();
  const digest = await crypto.subtle.digest("SHA-256", await blob.arrayBuffer());
  const hash = Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, "0")).join("");
  const key = await retryKey("voice-answer", { field, hash, mime, choices }, request.echobillSessionEpoch);
  const form = new FormData();
  form.append("field", field);
  const extension = mime.includes("mp4") ? "mp4" : mime.includes("ogg") ? "ogg" : mime.includes("wav") ? "wav" : "webm";
  form.append("file", blob, `answer.${extension}`);
  if (choices) form.append("choices", choices.join(","));
  try {
    const { data } = await api.post<VoiceAnswer>("/api/v1/ledger/voice-answer", form, {
      ...request,
      headers: { "Content-Type": undefined, "X-Idempotency-Key": key.value }, timeout: AUDIO_TIMEOUT_MS,
    });
    if (data.field !== field || !["ok", "failed"].includes(data.status)) throw new Error("INVALID_VOICE_ANSWER");
    releaseRetryAttempt(key);
    return data;
  } catch (error) {
    if (axios.isCancel(error) && !key.reused) releaseRetryAttempt(key);
    if (axios.isAxiosError(error) && error.response && error.response.status < 500 &&
        ![401, 429].includes(error.response.status) && error.response.data?.error_code !== "IDEMPOTENCY_IN_PROGRESS")
      releaseRetryAttempt(key);
    throw error;
  }
}
