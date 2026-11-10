import { useRef } from "react";
import type { BusinessInfoRequest } from "../api/tax";
import { BusinessInfoSheet } from "../ui/BusinessInfoSheet";
import type { ApiFailure } from "../utils/apiError";
import { RequestError } from "./RequestError";
import { createRequestKey } from "../utils/requestKey";
import { exceedsTextLimit, NAME_MAX_CHARS } from "../utils/textLimits";
interface Props { initialName?: string; busy: boolean; error: string | null; failure?: ApiFailure | null; onSave: (body: BusinessInfoRequest, retryKey: string) => Promise<void>; onClose: () => void; }
/** A registration retry key and the original business number stay only in dialog memory. */
export function BusinessInfoDialog({ initialName, busy, error, failure, onSave, onClose }: Props) {
  const attempt = useRef<{ signature: string; key: string; body: BusinessInfoRequest } | null>(null);
  return <><BusinessInfoSheet open initialName={initialName} submitting={busy} error={error} onClose={() => { if (!busy) onClose(); }}
    onSubmit={({ name, numberDigits }) => {
      if (busy || !/^\d{10}$/.test(numberDigits) || exceedsTextLimit(name, NAME_MAX_CHARS)) return;
      const body = { business_name: name.trim() || null, business_number: numberDigits };
      const signature = JSON.stringify(body);
      if (attempt.current?.signature !== signature) attempt.current = { signature, key: createRequestKey(), body };
      void onSave(body, attempt.current.key);
    }} />{failure && failure.kind !== "input" && <RequestError failure={failure} retry={() => {
      if (!busy && attempt.current) void onSave(attempt.current.body, attempt.current.key);
    }} />}</>;
}
