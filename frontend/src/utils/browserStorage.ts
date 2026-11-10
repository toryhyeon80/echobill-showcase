type StoreKind = "localStorage" | "sessionStorage";
const pending = { localStorage: new Map<string, string | null>(), sessionStorage: new Map<string, string | null>() };
const degraded = new Set<StoreKind>();
const listeners = new Set<() => void>();

/** Only expected browser storage restrictions use the in-memory fallback. */
function restricted(error: unknown, kind: StoreKind): void {
  if (!(error instanceof DOMException) || !["SecurityError", "QuotaExceededError", "NS_ERROR_DOM_QUOTA_REACHED"].includes(error.name)) throw error;
  if (!degraded.has(kind)) {
    degraded.add(kind);
    // Storage can fail during React render; notify after that render completes.
    queueMicrotask(() => listeners.forEach((listener) => listener()));
  }
}

/** Preserve this page's drafts and retry keys when persistent storage is unavailable. */
function safeStorage(kind: StoreKind): Storage {
  const memory = pending[kind];
  const cache = new Map<string, string>();
  const keys = () => {
    let stored: string[] = [];
    try {
      const backing = globalThis[kind];
      stored = Array.from({ length: backing.length }, (_, index) => backing.key(index)).filter((key): key is string => key !== null);
    } catch (error) { restricted(error, kind); }
    return [...new Set([...stored, ...cache.keys(), ...memory.keys()])].filter((key) => !memory.has(key) || memory.get(key) !== null);
  };
  const store: Storage = {
    get length() { return keys().length; },
    key: (index) => keys()[index] ?? null,
    getItem: (key) => {
      if (memory.has(key)) return memory.get(key) ?? null;
      try {
        const value = globalThis[kind].getItem(key);
        if (value === null) cache.delete(key); else cache.set(key, value);
        return value;
      }
      catch (error) { restricted(error, kind); return cache.get(key) ?? null; }
    },
    setItem: (key, value) => {
      cache.set(key, value);
      try { globalThis[kind].setItem(key, value); memory.delete(key); }
      catch (error) { restricted(error, kind); memory.set(key, value); }
    },
    removeItem: (key) => {
      cache.delete(key);
      try { globalThis[kind].removeItem(key); memory.delete(key); }
      catch (error) { restricted(error, kind); memory.set(key, null); }
    },
    clear: () => { keys().forEach((key) => store.removeItem(key)); },
  };
  return store;
}

export const localStore = safeStorage("localStorage");
export const sessionStore = safeStorage("sessionStorage");
/** OAuth must persist its nonce across full-page redirects; memory alone is insufficient. */
export function hasPersistentSessionValue(key: string, expected: string): boolean {
  try { return globalThis.sessionStorage.getItem(key) === expected; }
  catch (error) { restricted(error, "sessionStorage"); return false; }
}
export const storageRestricted = () => degraded.size > 0;
export const subscribeStorage = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener); }; };
