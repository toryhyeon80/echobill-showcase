import { useEffect, useRef, useState } from "react";
import { currentSessionEpoch, moveExistingCredit } from "../api/client";
import { MoveCreditView, type MoveCreditInput } from "../ui/MoveCreditView";
import { describeApiFailure } from "../utils/apiError";
import { useAuthStore, useToastStore } from "../stores/appStore";

interface Props {
  recentCounterparties?: string[];
  onClose: () => void;
  /** After a saved move — the caller reloads 받을 돈. */
  onMoved: () => void;
}

/**
 * 이미 있는 외상 옮기기 (PM 10/23): saves MoveCreditView's input through POST /ledger/moved-credit.
 * A retry of the same input reuses its idempotency key (postOnce), so a lost reply never saves it twice.
 */
export function MoveCreditDialog({ recentCounterparties, onClose, onMoved }: Props) {
  const mounted = useRef(true);
  const inFlight = useRef(false);
  const close = useRef(onClose);
  close.current = onClose;
  const [invalidSession, setInvalidSession] = useState(false);
  useEffect(() => {
    mounted.current = true;
    const invalidate = () => { setInvalidSession(true); close.current(); };
    window.addEventListener("echobill:session-changed", invalidate);
    return () => { mounted.current = false; window.removeEventListener("echobill:session-changed", invalidate); };
  }, []);
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(input: MoveCreditInput) {
    const owner = useAuthStore.getState().userId;
    if (inFlight.current || invalidSession || !owner) return;
    inFlight.current = true;
    const epoch = currentSessionEpoch();
    const current = () => mounted.current && epoch === currentSessionEpoch() && owner === useAuthStore.getState().userId;
    setSubmitting(true); setError(null);
    try {
      await moveExistingCredit(input);
      if (!current()) return;
      onMoved();
      useToastStore.getState().show(`${input.counterparty} ${input.amount.toLocaleString("ko-KR")}원을 옮겼어요`);
      onClose();
    } catch (failure) {
      if (current()) setError(describeApiFailure(failure).message);
    } finally {
      if (current()) { inFlight.current = false; setSubmitting(false); }
    }
  }

  if (invalidSession) return null;
  return <MoveCreditView recentCounterparties={recentCounterparties} submitting={submitting} error={error}
    onBack={() => { if (!submitting) onClose(); }} onSubmit={(input) => void submit(input)} />;
}
