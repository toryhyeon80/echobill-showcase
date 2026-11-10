import assert from "node:assert/strict";

/** Isolate drafts and browser lifecycle without real login or ledger writes. */
function storage(): Storage {
  const values = new Map<string, string>();
  return { getItem: (key) => values.get(key) ?? null, setItem: (key, value) => { values.set(key, value); },
    removeItem: (key) => { values.delete(key); }, clear: () => values.clear(),
    get length() { return values.size; }, key: (index) => [...values.keys()][index] ?? null };
}
Object.assign(globalThis, { localStorage: storage(), sessionStorage: storage(),
  window: Object.assign(new EventTarget(), { setTimeout, clearTimeout, setInterval, clearInterval }),
  document: new EventTarget() });

export const snapshot = { entry_id: "candidate", utterance_text: "잘못 들린 가게 배추 두 박스 만원 외상", counterparty: "잘못 들린 가게",
  items: [{ name: "배추", qty: 2, unit: "박스", unit_price: null, amount: 10000 }], total_amount: 10000, credit: true,
  created_at: "2026-10-16T00:00:00Z" };
export const noop = () => {};

/** Wait for the expected async state rather than depending on response timing. */
export async function until(ready: () => boolean): Promise<void> {
  for (let attempt = 0; attempt < 100; attempt++) {
    if (ready()) return;
    await new Promise((resolve) => setTimeout(resolve, 5));
  }
  assert.fail("review fixture did not settle");
}
