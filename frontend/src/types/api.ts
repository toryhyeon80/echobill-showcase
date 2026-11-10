import type { components } from "./generated";

/** Types are derived from backend OpenAPI; nullable defaults are normalized at the API edge. */
export type ApiLedgerItem = components["schemas"]["LedgerItem"];
export type ApiParseCandidate = components["schemas"]["ParseCandidateResponse"];
export type ParseRequest = components["schemas"]["ParseRequest"];
export type TokenResponse = components["schemas"]["TokenResponse"];
/** DL-32 authentication contracts from the current backend OpenAPI. */
export type LoginResponse = components["schemas"]["LoginResponse"];
export type SignupRequiredResponse = components["schemas"]["SignupRequiredResponse"];
export type KakaoLoginResponse = LoginResponse | SignupRequiredResponse;
export type LedgerEntry = components["schemas"]["LedgerEntryResponse"];
export type LedgerList = components["schemas"]["LedgerListResponse"];
export type MovedCreditEntry = components["schemas"]["LedgerListEntry"];
export type UnpaidList = components["schemas"]["UnpaidListResponse"];
export type CounterpartyDetail = components["schemas"]["CounterpartyDetailResponse"];
export type FeedbackRequest = components["schemas"]["FeedbackRequest"];

export type LedgerItem = Pick<ApiLedgerItem, "name"> & {
  qty: string | number | null;
  unit: string | null;
  unit_price: number | null;
  amount: number | null;
};

export interface CandidateFields {
  counterparty: string | null;
  items: LedgerItem[];
  total_amount: number | null;
  credit: boolean | null;
}

export type ParseCandidate = Omit<ApiParseCandidate, keyof CandidateFields> & CandidateFields;
/** Review fields fetched from either parse or owned-entry lookup; no invented parser metadata. */
export type CandidateSnapshot = Pick<ParseCandidate, "entry_id" | "utterance_text"> & CandidateFields & { created_at?: string };

/** UI boundary for partial error candidates; nullable fields retain their meaning. */
export type PartialCandidate = Partial<Omit<CandidateFields, "items">> & {
  items?: Array<Partial<LedgerItem>>;
};

export type ApiErrorResponse = Omit<components["schemas"]["ErrorResponse"], "partial_candidate"> & {
  partial_candidate?: PartialCandidate | null;
};
