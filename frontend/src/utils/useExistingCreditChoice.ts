import { useSyncExternalStore } from "react";
import { localStore } from "./browserStorage";

const changed = "echobill:existing-credit-choice";
const key = (owner: string) => `echobill_existing_credit_skip:${encodeURIComponent(owner)}`;

/** Keep the first-use choice across home visits; each account has its own device preference. */
export function useExistingCreditChoice(owner: string | null) {
  const skipped = useSyncExternalStore(subscribe, () => !!owner && localStore.getItem(key(owner)) === "yes", () => false);
  return [skipped, () => {
    if (!owner) return;
    localStore.setItem(key(owner), "yes");
    window.dispatchEvent(new Event(changed));
  }] as const;
}

/** Refresh the choice after a local action or another tab changes browser storage. */
function subscribe(listener: () => void) {
  window.addEventListener(changed, listener);
  window.addEventListener("storage", listener);
  return () => {
    window.removeEventListener(changed, listener);
    window.removeEventListener("storage", listener);
  };
}
