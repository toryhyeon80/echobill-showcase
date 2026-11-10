import { localStore } from "./browserStorage";
import type { ApiFailure } from "./apiError";
import type { CandidateDraft } from "./candidateDraft";
import type { ClarificationField } from "./clarification";
import { readDraft, writeDraft } from "./draftStorage";

const KEY = "echobill_repair_draft";
export interface RepairProgress { field?: ClarificationField; fullForm?: boolean }
export interface RepairSnapshot {
  owner: string;
  draft: CandidateDraft;
  utterance: string;
  transcript: string | null;
  repairOf?: string;
  failure: ApiFailure;
  progress: RepairProgress;
}

/** Restore only well-formed, unexpired correction drafts belonging to this login. */
export function readRepairDraft(owner: string | null): RepairSnapshot | null {
  if (!owner) return null;
  const value = readDraft(localStore, KEY);
  if (!value || typeof value !== "object") return null;
  const data = value as Partial<RepairSnapshot>;
  const draft = data.draft;
  const valid = owner && data.owner === owner && typeof data.utterance === "string" &&
    (data.transcript === null || typeof data.transcript === "string") &&
    (data.repairOf === undefined || typeof data.repairOf === "string") &&
    data.failure?.kind === "input" && typeof data.failure.message === "string" &&
    (data.failure.errorCode === undefined || typeof data.failure.errorCode === "string" &&
      !["UNSUPPORTED_TRANSACTION", "MULTIPLE_COUNTERPARTIES_DETECTED", "EXCEED_MAX_ITEMS"].includes(data.failure.errorCode)) &&
    (!data.failure.problemFields || Array.isArray(data.failure.problemFields) && data.failure.problemFields.every((field) => typeof field === "string")) &&
    data.progress && typeof data.progress === "object" &&
    (data.progress.fullForm === undefined || typeof data.progress.fullForm === "boolean") &&
    (data.progress.field === undefined || typeof data.progress.field === "string" && /^(credit|counterparty|total_amount|items\[[0-2]\]\.qty)$/.test(data.progress.field)) &&
    draft && draft.candidateId === null && typeof draft.counterparty === "string" && typeof draft.total_amount === "string" &&
    (draft.credit === null || typeof draft.credit === "boolean") && Array.isArray(draft.items) && draft.items.length <= 3 &&
    draft.items.every((item) => item && typeof item === "object" &&
      ["name", "qty", "unit", "unit_price", "amount"].every((key) => typeof item[key as keyof typeof item] === "string"));
  if (!valid) { clearRepairDraft(); return null; }
  const index = data.progress?.field?.match(/^items\[(\d+)\]/)?.[1];
  if (index !== undefined && Number(index) >= (draft?.items.length ?? 0)) { clearRepairDraft(); return null; }
  return data as RepairSnapshot;
}

/** Save editable values only, without audio, credentials or business registration data. */
export function saveRepairDraft(snapshot: RepairSnapshot): void { writeDraft(localStore, KEY, snapshot); }
export function clearRepairDraft(): void { localStore.removeItem(KEY); }
