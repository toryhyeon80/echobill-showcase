import { sessionStore } from "../utils/browserStorage";
import { api, currentSessionEpoch, postOnce, releaseRetryAttempt } from "./client";
import type { components } from "../types/generated";

export type BusinessInfo = components["schemas"]["BusinessInfoResponse"];
export type BusinessInfoRequest = components["schemas"]["BusinessInfoRequest"];
export type TaxInvoice = components["schemas"]["TaxInvoiceResponse"];
type InvoiceList = components["schemas"]["TaxInvoiceListResponse"];

/** Read masked business information; never persist the original number in the browser. */
export const getBusinessInfo = async () => (await api.get<BusinessInfo>("/api/v1/tax/business")).data;
/** The dialog owns this retry UUID in memory: no body hash of a 10-digit identifier is stored. */
export const registerBusinessInfo = async (body: BusinessInfoRequest, idempotencyKey: string) =>
  (await api.post<BusinessInfo>("/api/v1/tax/business", body, { headers: { "X-Idempotency-Key": idempotencyKey } })).data;
/** Retire only the attempts present when this session's status read began. */
function issueRetryCleanup() {
  const epoch = currentSessionEpoch();
  const keys = Array.from({ length: sessionStore.length }, (_, index) => sessionStore.key(index));
  const attempts = keys.flatMap((storageKey) => {
    if (!storageKey?.startsWith("echobill_retry_tax:issue:") || storageKey.endsWith("_pending")) return [];
    const value = sessionStore.getItem(storageKey);
    return value ? [{ storageKey, value }] : [];
  });
  return (invoices: TaxInvoice[]) => {
    if (epoch !== currentSessionEpoch()) return;
    const resolved = invoices.filter((invoice) => invoice.state !== "none")
      .map((invoice) => `echobill_retry_tax:issue:${invoice.entry_id}_`);
    attempts.forEach((attempt) => {
      if (resolved.some((prefix) => attempt.storageKey.startsWith(prefix))) releaseRetryAttempt(attempt);
    });
  };
}
export const getTaxInvoices = async () => {
  const cleanup = issueRetryCleanup();
  const list = (await api.get<InvoiceList>("/api/v1/tax")).data.invoices;
  cleanup(list);
  return list;
};
export const getTaxInvoice = async (entryId: string) => {
  const cleanup = issueRetryCleanup();
  const invoice = (await api.get<TaxInvoice>(`/api/v1/tax/${encodeURIComponent(entryId)}`)).data;
  cleanup([invoice]);
  return invoice;
};
/** A lost request retries the same key; status reads use GET rather than replayed POST snapshots. */
export const issueTaxInvoice = (entryId: string) => postOnce<TaxInvoice>(`tax:issue:${entryId}`, "/api/v1/tax/issue", { entry_id: entryId });
