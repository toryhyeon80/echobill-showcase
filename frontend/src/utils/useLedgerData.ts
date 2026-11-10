import { useEffect, useState } from "react";
import { currentSessionEpoch, getLedger, getUnpaid } from "../api/client";
import { useAuthStore } from "../stores/appStore";
import type { LedgerList, UnpaidList } from "../types/api";
import { describeApiFailure, type ApiFailure } from "./apiError";
import { takeStartupLedger } from "./startupLedger";

/** Fetch one user's confirmed ledger and balances; ignore stale requests. */
export function useLedgerData(month: string, includeUnpaid = true) {
  const { userId, ready } = useAuthStore();
  const epoch = currentSessionEpoch();
  const [revision, setRevision] = useState(0);
  const [data, setData] = useState<{ owner: string; epoch: number; month: string; ledger: LedgerList | null; unpaid: UnpaidList | null } | null>(null);
  const [busy, setBusy] = useState(true);
  const [error, setError] = useState<{ epoch: number; value: ApiFailure | null } | null>(null);
  useEffect(() => {
    let cancelled = false;
    setError(null); setBusy(true);
    if (!ready) return;
    if (!userId) { setData(null); setBusy(false); return; }
    const current = () => !cancelled && currentSessionEpoch() === epoch && useAuthStore.getState().userId === userId;
    const startup = includeUnpaid ? takeStartupLedger(month, userId) : null;
    void (startup ?? Promise.allSettled([getLedger(month), includeUnpaid ? getUnpaid() : Promise.resolve(null)])).then(([entries, balances]) => {
      if (!current()) return;
      const failed = [entries, balances].find((result) => result.status === "rejected");
      setError({ epoch, value: failed?.status === "rejected" ? describeApiFailure(failed.reason) : null });
      setData((previous) => ({ owner: userId, epoch, month,
        ledger: entries.status === "fulfilled" ? entries.value : previous?.owner === userId && previous.epoch === epoch && previous.month === month ? previous.ledger : null,
        unpaid: balances.status === "fulfilled" ? balances.value : previous?.owner === userId && previous.epoch === epoch ? previous.unpaid : null,
      }));
    })
      .finally(() => { if (current()) setBusy(false); });
    return () => { cancelled = true; };
  }, [month, revision, ready, userId, includeUnpaid, epoch]);
  const visible = ready && data?.owner === userId && data.epoch === epoch;
  return { ledger: visible && data.month === month ? data.ledger : null, unpaid: visible ? data.unpaid : null,
    busy: !ready || busy || (!!userId && (!visible || data.month !== month)), error: ready && userId && error?.epoch === epoch ? error.value : null,
    userId, epoch, reload: () => setRevision((value) => value + 1) };
}

export const kstToday = () => new Intl.DateTimeFormat("en-CA", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit" }).format(new Date());

/** Accept only real calendar dates before using navigation state in ledger API queries. */
export function ledgerNavigationDate(value: unknown): string | undefined {
  if (typeof value !== "string" || !/^\d{4}-\d{2}-\d{2}$/.test(value)) return undefined;
  const parsed = new Date(`${value}T00:00:00Z`);
  return Number.isFinite(parsed.getTime()) && parsed.toISOString().slice(0, 10) === value ? value : undefined;
}

/** Server UTC dates (including legacy naive ISO) are displayed in KST, never browser local time. */
export function kstRegistrationLabel(createdAt: string): string {
  const utc = /(?:Z|[+-]\d{2}:\d{2})$/i.test(createdAt) ? createdAt : `${createdAt}Z`;
  return new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", year: "numeric", month: "2-digit", day: "2-digit", weekday: "short" }).format(new Date(utc));
}
