import { useEffect, useState } from "react";
import { currentSessionEpoch } from "../api/client";
import { loadCounterpartyDirectory } from "./counterpartyDirectory";
import { useAuthStore } from "../stores/appStore";
import { kstToday } from "./useLedgerData";

interface RecentNames {
  owner: string;
  epoch: number;
  day: string;
  names: string[];
  failed: boolean;
}

let pending: { owner: string; epoch: number; day: string; request: ReturnType<typeof loadCounterpartyDirectory> } | null = null;

/** Share only overlapping reads in the same session; completed lists never become stale edit caches. */
function readDirectory(owner: string, epoch: number, day: string) {
  if (pending?.owner === owner && pending.epoch === epoch && pending.day === day) return pending.request;
  const request = loadCounterpartyDirectory(day).finally(() => {
    // An old session may finish after a new session has already started its own batch.
    if (pending?.request === request) pending = null;
  });
  pending = { owner, epoch, day, request };
  return request;
}

/** Names from receivables and 90 days of confirmed records; never persist customer names. */
export function useRecentCounterparties(active = true) {
  // Session replacement also emits when the user ID is unchanged; observe that update.
  const { userId: owner, ready } = useAuthStore();
  const epoch = currentSessionEpoch();
  const day = kstToday();
  const [result, setResult] = useState<RecentNames | null>(null);
  const [revision, setRevision] = useState(0);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    if (!active || !owner || !ready) { setResult(null); setLoading(false); return; }
    let cancelled = false;
    const current = () => !cancelled && useAuthStore.getState().userId === owner && currentSessionEpoch() === epoch;
    setLoading(true);
    setResult(null);
    void readDirectory(owner, epoch, day).then((directory) => {
      if (!current()) return;
      setResult({ owner, epoch, day, ...directory });
    }).catch(() => {
      if (current()) setResult({ owner, epoch, day, names: [], failed: true });
    }).finally(() => { if (current()) setLoading(false); });
    return () => { cancelled = true; };
  }, [owner, ready, epoch, day, revision, active]);
  const visible = active && !!owner && ready && result?.owner === owner && result.epoch === epoch && result.day === day;
  return { available: active && !!owner && ready, names: visible ? result.names : [], failed: visible && result.failed,
    loading: active && !!owner && ready && (loading || !visible), retry: () => setRevision((value) => value + 1) };
}
