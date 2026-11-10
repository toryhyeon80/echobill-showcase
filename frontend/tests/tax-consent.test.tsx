import { beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import axios, { AxiosError } from "axios";
import { api, retryKey, releaseRetryAttempt } from "../src/api/client";
import { useAuthStore } from "../src/stores/appStore";
import { issueTaxInvoice, registerBusinessInfo, getTaxInvoice, getTaxInvoices } from "../src/api/tax";
import { updateConsent } from "../src/api/consent";
function storage() {
  const values = new Map<string, string>();
  return { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key), clear: () => values.clear(), entries: () => [...values],
    get length() { return values.size; }, key: (index: number) => [...values.keys()][index] ?? null };
}
const retryStorage = storage();
Object.assign(globalThis, { localStorage: storage(), sessionStorage: retryStorage, window: new EventTarget() });
beforeEach(() => { localStorage.clear(); sessionStorage.clear(); useAuthStore.getState().setCookieSession(null); });
function failure(status: number, error_code: string, headers: Record<string, string> = {}) {
  return new AxiosError("request rejected", undefined, undefined, undefined,
    { status, data: { error_code }, headers, config: { headers: {} } } as never);
}

for (const list of [false, true]) {
  for (const transition of ["new-session", "new-attempt"] as const) {
    test(`late tax ${list ? "list" : "status"} cannot retire a newer retry key (${transition})`, async () => {
      const previous = api.defaults.adapter;
      useAuthStore.getState().setCookieSession("tax-owner");
      const body = { entry_id: "entry-tax" };
      const oldAttempt = await retryKey("tax:issue:entry-tax", body);
      let release: (() => void) | undefined;
      api.defaults.adapter = (config) => new Promise((resolve) => { release = () => {
        const invoice = { entry_id: "entry-tax", state: "requested", is_mock: true };
        resolve({ config, status: 200, statusText: "OK", headers: {}, data: list ? { invoices: [invoice] } : invoice });
      }; });
      try {
        const reading = list ? getTaxInvoices() : getTaxInvoice("entry-tax");
        await new Promise((resolve) => setTimeout(resolve, 0));
        assert.ok(release);
        if (transition === "new-session") useAuthStore.getState().setCookieSession("tax-owner");
        releaseRetryAttempt(oldAttempt);
        const newAttempt = await retryKey("tax:issue:entry-tax", body);
        assert.notEqual(newAttempt.value, oldAttempt.value);
        release(); await reading;
        assert.equal(sessionStorage.getItem(newAttempt.storageKey), newAttempt.value);
      } finally { release?.(); api.defaults.adapter = previous; }
    });
  }
}
test("consent PATCH uses its original value and key for a lost response retry", async () => {
  const previous = api.defaults.adapter;
  try {
    useAuthStore.getState().setCookieSession("owner", [], false);
    const requests: unknown[] = [];
    api.defaults.adapter = async (config) => {
      assert.equal(config.method, "patch"); assert.equal(config.url, "/api/v1/me/consent");
      requests.push({ body: JSON.parse(config.data), key: config.headers["X-Idempotency-Key"] });
      if (requests.length === 1) throw new AxiosError("lost", undefined, config);
      return { config, data: { ai_consent: true, ai_consent_at: "2026-10-12T00:00:00Z" }, status: 200, statusText: "OK", headers: {} };
    };
    await assert.rejects(updateConsent(true, "consent-key"));
    const result = await updateConsent(true, "consent-key");
    assert.equal(result.ai_consent, true); assert.deepEqual(requests[0], requests[1]);
    assert.equal(sessionStorage.length, 0);
  } finally { api.defaults.adapter = previous; }
});

test("mock invoice lost response reuses its key, while status recovery reads GET", async () => {
  const keys: string[] = [];
  let lost = true;
  api.defaults.adapter = async (config) => {
    if (config.method === "get") {
      assert.equal(config.url, "/api/v1/tax/entry-tax");
      return { status: 200, statusText: "OK", data: { entry_id: "entry-tax", state: "issued", is_mock: true }, headers: {}, config };
    }
    keys.push(String(config.headers["X-Idempotency-Key"]));
    assert.equal(config.url, "/api/v1/tax/issue");
    if (lost) { lost = false; throw new AxiosError("lost synthetic response"); }
    return { status: 200, statusText: "OK", data: { entry_id: "entry-tax", state: "requested", is_mock: true }, headers: {}, config };
  };
  await assert.rejects(issueTaxInvoice("entry-tax"));
  assert.equal((await issueTaxInvoice("entry-tax")).state, "requested");
  assert.equal(keys[0], keys[1]);
  const invoice = await getTaxInvoice("entry-tax");
  assert.equal(invoice.state, "issued");
  assert.equal(invoice.is_mock, true);
});

test("business-required rejection permits a new issue key after registration and stores no raw number", async () => {
  const issueKeys: string[] = [];
  let registered = false;
  api.defaults.adapter = async (config) => {
    if (config.url === "/api/v1/tax/business") {
      const stored = JSON.stringify(retryStorage.entries());
      assert.ok(!stored.includes("1234567891"));
      assert.ok(!stored.includes("가상사업자"));
      assert.ok(config.headers["X-Idempotency-Key"]);
      registered = true;
      return { status: 200, statusText: "OK", data: { registered: true, business_number_masked: "123-**-****1" }, headers: {}, config };
    }
    issueKeys.push(String(config.headers["X-Idempotency-Key"]));
    if (!registered) throw new AxiosError("register first", undefined, config, undefined,
      { status: 400, data: { error_code: "BUSINESS_INFO_REQUIRED" }, headers: {}, config });
    return { status: 200, statusText: "OK", data: { state: "issued", is_mock: true }, headers: {}, config };
  };
  await assert.rejects(issueTaxInvoice("entry-tax"));
  await registerBusinessInfo({ business_number: "1234567891", business_name: "가상사업자" }, crypto.randomUUID());
  await issueTaxInvoice("entry-tax");
  assert.notEqual(issueKeys[0], issueKeys[1]);
});

test("lost business registration leaves no identifier fingerprint in browser storage", async () => {
  const before = JSON.stringify(retryStorage.entries());
  api.defaults.adapter = async (config) => {
    assert.equal(config.headers["X-Idempotency-Key"], "dialog-memory-retry-key");
    throw new AxiosError("lost registration");
  };
  await assert.rejects(registerBusinessInfo({ business_number: "1234567891" }, "dialog-memory-retry-key"));
  assert.equal(JSON.stringify(retryStorage.entries()), before);
  assert.equal(localStorage.length, 0);
});

test("GET recovery retires an uncertain issue key before a later failed attempt is retried", async () => {
  const keys: string[] = [];
  let state = "requested";
  api.defaults.adapter = async (config) => {
    if (config.method === "get") return { status: 200, statusText: "OK", data: { entry_id: "entry-tax", state }, headers: {}, config };
    keys.push(String(config.headers["X-Idempotency-Key"]));
    if (keys.length === 1) throw new AxiosError("lost issue");
    return { status: 200, statusText: "OK", data: { state: "requested" }, headers: {}, config };
  };
  await assert.rejects(issueTaxInvoice("entry-tax"));
  assert.equal((await getTaxInvoice("entry-tax")).state, "requested");
  state = "failed";
  await getTaxInvoice("entry-tax");
  await issueTaxInvoice("entry-tax");
  assert.notEqual(keys[0], keys[1]);
});
