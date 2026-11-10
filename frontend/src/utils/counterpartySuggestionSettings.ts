import { useSyncExternalStore } from "react";
import { currentSessionEpoch } from "../api/client";
import { localStore } from "./browserStorage";

const key = "echobill_counterparty_suggestions";
const listeners = new Set<() => void>();
const enabled = () => localStore.getItem(key) === "on";
function subscribe(listener: () => void) {
  listeners.add(listener);
  const changed = (event: StorageEvent) => { if (event.key === key || event.key === null) listener(); };
  window.addEventListener("storage", changed);
  return () => { listeners.delete(listener); window.removeEventListener("storage", changed); };
}

/** Off until the comparison policy and real-device checks pass; only this preference is persisted. */
export function saveCounterpartySuggestions(enabled: boolean) {
  localStore.setItem(key, enabled ? "on" : "off");
  listeners.forEach((listener) => listener());
}
export const useCounterpartySuggestions = () => useSyncExternalStore(subscribe, enabled, () => false);

type Event = "shown" | "selected" | "undone";
let epoch = -1;
let counts = { shown: 0, selected: 0, undone: 0 };
/** Session-local evaluation counts only: no names, utterances, entry IDs or network calls. */
export function getSuggestionCounts() {
  if (epoch !== currentSessionEpoch()) { epoch = currentSessionEpoch(); counts = { shown: 0, selected: 0, undone: 0 }; }
  return { ...counts };
}
export function trackSuggestion(event: Event) {
  getSuggestionCounts();
  counts[event]++;
}
