import { useEffect, useState } from "react";
import { currentSessionEpoch } from "../api/client";
import { getBusinessInfo } from "../api/tax";
import { useAuthStore } from "../stores/appStore";

/** Optional copy signature, fetched only for an open detail and never persisted. */
export function useBusinessName(active: boolean): string | null {
  const { userId, ready } = useAuthStore();
  const epoch = currentSessionEpoch();
  const [result, setResult] = useState<{ owner: string; epoch: number; name: string | null } | null>(null);
  useEffect(() => {
    setResult(null);
    if (!active || !ready || !userId) return;
    let cancelled = false;
    const current = () => !cancelled && useAuthStore.getState().userId === userId && currentSessionEpoch() === epoch;
    void getBusinessInfo().then((info) => {
      if (current()) setResult({ owner: userId, epoch, name: info.registered ? info.business_name?.trim() || null : null });
    }).catch(() => {
      // A signature is optional. A failed lookup must not prevent reviewing or copying the debt.
      if (current()) setResult({ owner: userId, epoch, name: null });
    });
    return () => { cancelled = true; };
  }, [active, ready, userId, epoch]);
  return active && ready && result?.owner === userId && result.epoch === epoch ? result.name : null;
}
