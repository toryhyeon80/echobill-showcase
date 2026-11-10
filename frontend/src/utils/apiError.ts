import axios from "axios";
import type { ApiErrorResponse, PartialCandidate } from "../types/api";
import type { ErrorKind } from "../ui/ErrorNotice";

/** Map HTTP failures to the shared design screens without confusing pending work with conflict. */
export function errorNoticeKind(failure: ApiFailure): ErrorKind | null {
  if (failure.kind === "network" || failure.kind === "server" || failure.kind === "rate") return failure.kind;
  if (failure.kind === "missing") return "notFound";
  if (failure.httpStatus === 413) return "tooLong";
  if (failure.kind === "conflict") return failure.errorCode === "CANDIDATE_EXPIRED" ? "expired"
    : failure.errorCode === "REPAIR_ALREADY_CONFIRMED" ? "alreadyConfirmed"
    : failure.errorCode === "ENTRY_NOT_CONFIRMED" ? "notConfirmed" : "conflict";
  return null;
}

export interface ApiFailure {
  kind: "input" | "auth" | "missing" | "conflict" | "pending" | "rate" | "external" | "server" | "network";
  message: string;
  errorCode?: string;
  problemFields?: string[];
  partialCandidate?: PartialCandidate;
  utteranceText?: string;
  redirect?: ApiErrorResponse["redirect"];
  requestKey?: string;
  retryAfter?: number;
  httpStatus?: number;
}

/** Read the API error envelope without treating unrelated failures as input mistakes. */
export function describeApiFailure(error: unknown): ApiFailure {
  if (!axios.isAxiosError(error) || !error.response) {
    return { kind: "network", message: "서버에 연결되지 않았습니다. 연결을 확인한 뒤 다시 시도해 주세요." };
  }
  const status = error.response.status;
  const body: unknown = error.response.data;
  const envelope = body && typeof body === "object" && "detail" in body &&
    typeof body.detail === "object" ? body.detail : body;
  const detail = envelope && typeof envelope === "object" ? envelope as Partial<ApiErrorResponse> : null;
  const message = typeof detail?.message === "string" ? detail.message : undefined;
  const errorCode = typeof detail?.error_code === "string" ? detail.error_code : undefined;
  const problemFields = Array.isArray(detail?.problem_fields) ? detail.problem_fields.filter((field): field is string => typeof field === "string") : [];
  const partialCandidate = detail?.partial_candidate && typeof detail.partial_candidate === "object" ? detail.partial_candidate : undefined;
  const header = error.config?.headers?.["X-Idempotency-Key"];
  const requestKey = typeof header === "string" ? header : undefined;

  if (status === 400 || status === 422) {
    if (status === 400 && errorCode === "NOT_A_SALE") {
      return { kind: "input", errorCode, problemFields: [], redirect: detail?.redirect,
        message: message ?? "이 내용은 새 판매로 적을 수 없어요. 장부에서 확인해 주세요." };
    }
    // DL-30 cannot be repaired into a sale: ignore any unexpected partial fields.
    if (status === 400 && errorCode === "UNSUPPORTED_TRANSACTION") {
      return { kind: "input", errorCode, problemFields: [],
        message: "판매·외상·입금만 기록할 수 있어요. 판매 내용으로 다시 말씀해 주세요.",
        utteranceText: typeof detail?.utterance_text === "string" && detail.utterance_text.trim() ? detail.utterance_text : undefined };
    }
    const utteranceText = status === 400 && errorCode !== "AUDIO_INVALID" &&
      typeof detail?.utterance_text === "string" && detail.utterance_text.trim() ? detail.utterance_text : undefined;
    return { kind: "input", message: message ?? "입력 내용을 확인하고 수정해 주세요.", errorCode, problemFields, partialCandidate, utteranceText, requestKey };
  }
  if (status === 401) return { kind: "auth", message: "로그인이 만료되었습니다. 다시 로그인해 주세요." };
  if (status === 404) return { kind: "missing", message: "찾을 수 없습니다." };
  if (status === 409) {
    if (errorCode === "IDEMPOTENCY_IN_PROGRESS") return { kind: "pending", errorCode, message: "처리 결과를 확인 중입니다. 잠시 후 같은 요청으로 다시 확인해 주세요." };
    const fallback = errorCode === "REPAIR_ALREADY_CONFIRMED" ? "이미 장부에 저장된 내용이에요. 장부에서 확인해 주세요."
      : errorCode === "ENTRY_NOT_CONFIRMED" ? "장부를 먼저 확정해 주세요." : errorCode === "CANDIDATE_EXPIRED"
      ? "7일이 지난 후보는 확정할 수 없습니다. 다시 입력해 주세요."
      : "이 요청을 다시 처리할 수 없습니다. 새로 입력해 주세요.";
    return { kind: "conflict", message: message ?? fallback, errorCode };
  }
  if (status === 413) return { kind: "external", httpStatus: status, errorCode, message: "녹음이 너무 깁니다. 짧게 나누거나 텍스트로 입력해 주세요." };
  if (status === 429) {
    const seconds = Number(error.response.headers["retry-after"]);
    return { kind: "rate", errorCode, retryAfter: Number.isFinite(seconds) && seconds > 0 ? Math.ceil(seconds) : undefined, message: Number.isFinite(seconds) && seconds > 0
      ? `${Math.ceil(seconds)}초 후 다시 시도해 주세요.` : "요청이 많습니다. 잠시 기다린 뒤 다시 시도해 주세요." };
  }
  if (status === 503 || status === 504) return { kind: "external", message: message ?? "음성 처리가 지연되었습니다. 다시 시도하거나 텍스트로 입력해 주세요." };
  return { kind: "server", message: message ?? "서버에 문제가 생겼습니다. 잠시 후 다시 시도해 주세요.", errorCode };
}
