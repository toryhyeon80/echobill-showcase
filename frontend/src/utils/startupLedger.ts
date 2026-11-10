import { currentSessionEpoch, getLedger, getUnpaid } from "../api/client";
import type { LedgerList, UnpaidList } from "../types/api";

interface StartupRead {
  month: string;
  epoch: number;
  owner: string | null;
  result: Promise<[PromiseSettledResult<LedgerList>, PromiseSettledResult<UnpaidList>]>;
}
let pending: StartupRead | null = null;

/** A confirmed write makes any earlier startup balances stale. */
export function invalidateStartupLedger(): void { pending = null; }

/** Read-only cookie-authenticated requests start beside /auth/me; no data is shown before it succeeds. */
export function startStartupLedger(month: string) {
  const epoch = currentSessionEpoch();
  if (!pending || pending.month !== month || pending.epoch !== epoch) {
    pending = { month, epoch, owner: null, result: Promise.allSettled([getLedger(month, true), getUnpaid(true)]) };
  }
  const read = pending;
  return {
    adopt(owner: string) { if (pending === read && epoch === currentSessionEpoch()) read.owner = owner; },
    discard() { if (pending === read) pending = null; },
  };
}

/** Share only this verified owner's startup result, including React's immediate effect replay. */
export function takeStartupLedger(month: string, owner: string) {
  const read = pending;
  if (!read || read.month !== month || read.owner !== owner || read.epoch !== currentSessionEpoch()) return null;
  void read.result.then(() => setTimeout(() => { if (pending === read) pending = null; }, 0));
  return read.result;
}
