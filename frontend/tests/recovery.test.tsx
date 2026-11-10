import { useState } from "react";
import { create, act, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { DesignShell } from "../src/components/DesignShell";

import { RetryNotice } from "../src/ui/RetryNotice";
import "./microphone.test";
import { localStore, sessionStore } from "../src/utils/browserStorage";
import { beforeEach, test } from "node:test";
import assert from "node:assert/strict";
import axios, { AxiosError } from "axios";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import { BusinessInfoDialog } from "../src/components/BusinessInfoDialog";
import { PaymentView } from "../src/ui/PaymentView";
import { api, parseUtterance, parseCorrectedCandidate, postOnce, recordPayment, reviewPaymentForNewInput, retryKey, releaseRetryAttempt, sendFeedback } from "../src/api/client";
import { describeApiFailure, errorNoticeKind } from "../src/utils/apiError";
import { RequestError } from "../src/components/RequestError";
import { ErrorDialog } from "../src/components/ErrorDialog";
import { RepairDialog } from "../src/components/RepairDialog";
import { answerClarification, applyVoiceAnswer, arithmeticChoices, clarificationQuestion, pendingClarifications, voiceField } from "../src/utils/clarification";
import { enabledVoiceFields, submitVoiceAnswer } from "../src/api/voiceAnswer";
import { readRepairDraft, saveRepairDraft } from "../src/utils/repairStorage";
import { restoreAppSession, guardCandidateResponse, useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { createCandidateDraft, saveCandidateDraft, restoreCandidateDraft, updateDraftItem, draftToCandidateFields } from "../src/utils/candidateDraft";
import { readDraft, writeDraft, clearSessionDrafts, readUtteranceDraft, saveUtteranceDraft } from "../src/utils/draftStorage";
import { createLoginUrl, consumeLoginCallback } from "../src/utils/kakaoLogin";
import { parseRecording } from "../src/api/client";
import { ConfirmView } from "../src/ui/ConfirmView";
import { kstRegistrationLabel, ledgerNavigationDate } from "../src/utils/useLedgerData";
import type { LedgerList } from "../src/types/api";
import { candidateView, calendarMarks } from "../src/utils/viewModels";
import { startStartupLedger, takeStartupLedger } from "../src/utils/startupLedger";

test("ledger navigation rejects malformed and impossible dates before building a month query", () => {
  for (const value of [undefined, null, 123, "NaN-NaN", "2026-13-01", "2026-02-30", "2026-2-03", "2026-01-00", "2026-10-14extra"])
    assert.equal(ledgerNavigationDate(value), undefined);
  assert.equal(ledgerNavigationDate("2026-10-14"), "2026-10-14");
  assert.equal(ledgerNavigationDate("2028-02-29"), "2028-02-29");
});

test("full correction form uses a visible design button and blocks repeated submission", () => {
  const draft = createCandidateDraft({ ...snapshot, credit: true });
  const props = { message: "수정해 주세요", draft, problemFields: [], submitError: null, onChange() {}, onSubmit() {}, onClose() {} };
  const html = renderToStaticMarkup(<RepairDialog {...props} busy={false} />);
  assert.match(html, /class="[^"]*bg-brand[^"]*text-white[^\"]*"[^>]*>수정 내용 제출/);
  assert.doesNotMatch(html, /class="btn/);
  const locked = renderToStaticMarkup(<RepairDialog {...props} busy />);
  assert.match(locked, /disabled=""[^>]*>확인 중/);
});

test("startup ledger requests run together and stay private until authentication binds their owner", async () => {
  const releases: Array<() => void> = [];
  const seen: string[] = [];
  api.defaults.adapter = async (config) => new Promise((resolve) => {
    seen.push(config.url!);
    releases.push(() => resolve({ data: config.url!.endsWith("/ledger") ? { month: "2026-10", entries: [], daily_credit: [] } : { total_unpaid: 0, counterparties: [] }, status: 200, statusText: "OK", headers: {}, config }));
  });
  const request = startStartupLedger("2026-10");
  assert.equal(takeStartupLedger("2026-10", "user-a"), null);
  await new Promise((resolve) => setTimeout(resolve, 0));
  assert.deepEqual(seen.sort(), ["/api/v1/dashboard/unpaid", "/api/v1/ledger"]);
  request.adopt("user-a");
  const result = takeStartupLedger("2026-10", "user-a");
  assert.ok(result);
  assert.equal(takeStartupLedger("2026-10", "user-b"), null);
  releases.forEach((release) => release());
  assert.deepEqual((await result).map((row) => row.status), ["fulfilled", "fulfilled"]);
  request.discard();
});

test("startup data cannot cross a changed cookie session", async () => {
  api.defaults.adapter = async (config) => ({ data: {}, status: 200, statusText: "OK", headers: {}, config });
  const request = startStartupLedger("2026-10");
  useAuthStore.getState().setCookieSession("another-user");
  request.adopt("another-user");
  assert.equal(takeStartupLedger("2026-10", "another-user"), null);
  request.discard();
});

test("startup 401 waits for auth/me while an ordinary ledger 401 expires the session", async () => {
  let expired = 0;
  const listener = () => { expired += 1; };
  window.addEventListener("echobill:session-expired", listener);
  api.defaults.adapter = async (config) => { throw new AxiosError("unauthorized", undefined, config, undefined, { data: {}, status: 401, statusText: "Unauthorized", headers: {}, config }); };
  try {
    const { getLedger } = await import("../src/api/client");
    await assert.rejects(getLedger("2026-10", true));
    assert.equal(expired, 0);
    await assert.rejects(getLedger("2026-10"));
    assert.equal(expired, 1);
  } finally { window.removeEventListener("echobill:session-expired", listener); }
});

test("business editing preserves the known name while the original number starts empty", () => {
  const html = renderToStaticMarkup(<BusinessInfoDialog initialName="시연상사" busy={false} error={null} onSave={async () => {}} onClose={() => {}} />);
  assert.match(html, /value="시연상사"/);
  assert.match(html, /사업자등록번호/);
  const inputs = [...html.matchAll(/<input\b[^>]*>/g)];
  assert.equal(inputs.length, 2);
  assert.match(inputs[1][0], /value=""/);
});

function storage() {
  const values = new Map<string, string>();
  return { getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value), removeItem: (key: string) => values.delete(key), clear: () => values.clear(), entries: () => [...values],
    get length() { return values.size; }, key: (index: number) => [...values.keys()][index] ?? null };
}
const retryStorage = storage();
Object.assign(globalThis, { localStorage: storage(), sessionStorage: retryStorage, window: new EventTarget() });
beforeEach(() => { localStorage.clear(); sessionStorage.clear(); useAuthStore.getState().setCookieSession(null); });

const snapshot = { entry_id: "draft-entry", utterance_text: "테스트", counterparty: "테스트상사",
  items: [{ name: "배추", qty: 2, unit: null, unit_price: null, amount: 1000 }], total_amount: 1000, credit: true };

test("answer microphones follow server capabilities and clear on logout, not stale build settings", async () => {
  useAuthStore.getState().setCookieSession("owner", ["qty", "invalid", "credit"]);
  assert.deepEqual(enabledVoiceFields(), ["credit", "qty"]);
  const previous = api.defaults.adapter;
  api.defaults.adapter = async (config) => ({ status: 200, statusText: "OK", headers: {}, config,
    data: { user_id: "owner", expires_in_hours: 24, voice_answer_fields: ["total"] } });
  await useAuthStore.getState().hydrateFromStorage();
  assert.deepEqual(enabledVoiceFields(), ["total"]);
  api.defaults.adapter = async (config) => ({ status: 200, statusText: "OK", headers: {}, config,
    data: { user_id: "owner", expires_in_hours: 24 } });
  await useAuthStore.getState().hydrateFromStorage();
  assert.deepEqual(enabledVoiceFields(), []);
  useAuthStore.getState().setCookieSession(null);
  assert.deepEqual(enabledVoiceFields(), []);
  api.defaults.adapter = previous;
});

test("a number-only quantity answer keeps the question unit and never invents one", () => {
  const withUnit = createCandidateDraft({ ...snapshot, items: [{ ...snapshot.items[0], qty: null, unit: "단" }] });
  const answer = { field: "qty" as const, status: "ok" as const, value: "3", unit: null };
  const next = applyVoiceAnswer(withUnit, "items[0].qty", answer)!;
  assert.equal(next.items[0].unit, "단"); assert.equal(next.items[0].qty, "3");
  assert.equal(applyVoiceAnswer(createCandidateDraft({ ...snapshot, items: [{ ...snapshot.items[0], qty: null }] }), "items[0].qty", answer)?.items[0].unit, "");
  assert.equal(candidateView(next, "테스트", "등록일").items[0].unitPrice, null);
});

test("repair links the rejected text or audio request to a fresh manual submission", async () => {
  let originalKey = "";
  api.defaults.adapter = async (config) => {
    originalKey = String(config.headers["X-Idempotency-Key"]);
    throw new AxiosError("missing", undefined, config, undefined, { status: 400, statusText: "Bad Request", headers: {}, config,
      data: { error_code: "MISSING_REQUIRED_FIELD", message: "외상 여부", partial_candidate: snapshot, problem_fields: ["credit"] } });
  };
  for (const original of [() => parseUtterance("테스트 거래"), () => parseRecording(new Blob(["audio"], { type: "audio/webm" }))]) {
    let failureKey: string | undefined;
    await assert.rejects(original(), (error) => { failureKey = describeApiFailure(error).requestKey; return true; });
    assert.equal(failureKey, originalKey);
    const originalAdapter = api.defaults.adapter;
    const repairKeys: string[] = [];
    api.defaults.adapter = async (config) => {
      const body = JSON.parse(String(config.data));
      assert.equal(body.repair_of, failureKey);
      assert.deepEqual(body.candidate, draftToCandidateFields(createCandidateDraft(snapshot)));
      repairKeys.push(String(config.headers["X-Idempotency-Key"]));
      assert.notEqual(repairKeys.at(-1), failureKey);
      if (repairKeys.length === 1) throw new AxiosError("lost response");
      return { status: 200, statusText: "OK", headers: {}, config, data: { ...snapshot, status: "candidate", parser_type: "manual" } };
    };
    await assert.rejects(parseCorrectedCandidate("테스트 거래", draftToCandidateFields(createCandidateDraft(snapshot)), failureKey));
    const repaired = await parseCorrectedCandidate("테스트 거래", draftToCandidateFields(createCandidateDraft(snapshot)), failureKey);
    assert.equal(repaired.status, "candidate");
    assert.equal(repairKeys[0], repairKeys[1]);
    api.defaults.adapter = originalAdapter;
  }
});

test("voice answer applies only a matching valid field and preserves other items", () => {
  const draft = createCandidateDraft({ ...snapshot, credit: null, items: [snapshot.items[0], { ...snapshot.items[0], qty: null }] });
  assert.equal(voiceField("items[1].qty"), "qty");
  assert.equal(voiceField("total_amount"), "total");
  for (const answer of [
    { field: "total" as const, status: "ok" as const, value: 3 },
    { field: "qty" as const, status: "failed" as const, value: 3 },
    { field: "qty" as const, status: "ok" as const, value: 0 },
    { field: "qty" as const, status: "ok" as const, value: true },
  ]) assert.equal(applyVoiceAnswer(draft, "items[1].qty", answer), null);
  const filled = applyVoiceAnswer(draft, "items[1].qty", { field: "qty", status: "ok", value: "2.5", unit: "박스" })!;
  assert.equal(filled.items[1].qty, "2.5");
  assert.equal(filled.items[1].unit, "박스");
  assert.deepEqual(filled.items[0], draft.items[0]);
  assert.equal(filled.total_amount, draft.total_amount);
  assert.equal(applyVoiceAnswer(draft, "credit", { field: "credit", status: "ok", value: "false" }), null);
  assert.equal(applyVoiceAnswer(draft, "credit", { field: "credit", status: "ok", value: false })?.credit, false);
  assert.deepEqual(enabledVoiceFields(), []);
  assert.deepEqual(enabledVoiceFields("credit, qty,invalid"), ["credit", "qty"]);
});

test("short audio uses its own endpoint and retries identical field and choices with the same key", async () => {
  const keys: string[] = [];
  api.defaults.adapter = async (config) => {
    assert.equal(config.url, "/api/v1/ledger/voice-answer");
    const form = config.data as FormData;
    assert.equal(form.get("field"), "total");
    assert.equal(form.get("choices"), "30000,35000");
    assert.ok(form.get("file") instanceof Blob);
    keys.push(String(config.headers["X-Idempotency-Key"]));
    if (keys.length === 1) throw new AxiosError("lost response");
    return { status: 200, statusText: "OK", headers: {}, config, data: { field: "total", status: "ok", value: 35000 } };
  };
  const audio = new Blob(["short audio"], { type: "audio/mp4" });
  await assert.rejects(submitVoiceAnswer("total", audio, [30000, 35000]));
  assert.equal((await submitVoiceAnswer("total", audio, [30000, 35000])).value, 35000);
  assert.equal(keys[0], keys[1]);
  await submitVoiceAnswer("total", audio, [30000, 35000]);
  assert.notEqual(keys[1], keys[2]);
});

test("correction recovery keeps question, original key and values, and clears on logout or owner change", () => {
  const value = { owner: "owner-a", draft: createCandidateDraft(snapshot), utterance: "거래 원문", transcript: null,
    repairOf: "original-key", failure: { kind: "input" as const, message: "보완", errorCode: "MISSING_REQUIRED_FIELD" },
    progress: { field: "credit" as const, fullForm: false } };
  saveRepairDraft(value);
  assert.equal(readRepairDraft(null), null);
  assert.deepEqual(readRepairDraft("owner-a"), value);
  assert.equal(readRepairDraft("owner-b"), null);
  assert.equal(readRepairDraft("owner-a"), null);
  saveRepairDraft(value);
  clearSessionDrafts();
  assert.equal(readRepairDraft("owner-a"), null);
  writeDraft(localStorage, "echobill_repair_draft", value, Date.now() - 7 * 24 * 60 * 60 * 1000);
  assert.equal(readRepairDraft("owner-a"), null);
});

test("repair without an optional error code survives refresh while malformed codes are rejected", () => {
  const draft = createCandidateDraft({ ...snapshot, credit: null });
  saveRepairDraft({ owner: "owner", draft, utterance: "테스트", transcript: null,
    failure: { kind: "input", message: "입력 확인" }, progress: { field: "credit" } });
  assert.equal(readRepairDraft("owner")?.draft.credit, null);
  writeDraft(localStorage, "echobill_repair_draft", { owner: "owner", draft, utterance: "테스트", transcript: null,
    failure: { kind: "input", message: "입력 확인", errorCode: 123 }, progress: {} });
  assert.equal(readRepairDraft("owner"), null);
});

test("arithmetic card offers stated total and exact line sum, while unresolved item contradictions keep the full form", () => {
  const draft = createCandidateDraft({ ...snapshot, total_amount: 1500 });
  assert.deepEqual(arithmeticChoices(draft), [1500, 1000]);
  const markup = renderToStaticMarkup(<RepairDialog message="금액 계산" errorCode="AMOUNT_MISMATCH" problemFields={["total_amount"]}
    draft={draft} busy={false} submitError={null} onChange={() => {}} onSubmit={() => {}} onClose={() => {}} />);
  assert.match(markup, /합계가 1,500원이에요, 1,000원이에요/);
  assert.doesNotMatch(markup, /수정 내용 제출/);
  assert.deepEqual(arithmeticChoices({ ...draft, items: [{ ...draft.items[0], qty: "" }] }), []);
  assert.deepEqual(arithmeticChoices({ ...draft, items: [{ ...draft.items[0], unit_price: "1000" }] }), []);
});

test("size limits and throttling guide alternative input without a correction form", () => {
  for (const [status, headers, expected] of [[413, {}, /녹음이 너무 깁니다/], [429, { "retry-after": "12" }, /12초 후/]] as const) {
    const config = { headers: new axios.AxiosHeaders() };
    const failure = describeApiFailure(new AxiosError("error", undefined, config, undefined, { status, statusText: "error", headers, config, data: {} }));
    assert.notEqual(failure.kind, "input");
    assert.match(failure.message, expected);
  }
});

test("DL-31 asks each missing field without inferring quantity or changing extracted amounts", () => {
  let draft = createCandidateDraft({ ...snapshot, credit: null, counterparty: null, total_amount: null,
    items: [{ name: "배추", qty: null, unit: "박스", unit_price: null, amount: null }] });
  assert.deepEqual(pendingClarifications(draft), ["credit", "items[0].qty", "total_amount", "counterparty"]);
  assert.equal(draft.items[0].qty, "");
  draft = answerClarification(draft, "credit", false);
  assert.equal(pendingClarifications(draft)[0], "items[0].qty");
  assert.equal(clarificationQuestion("items[0].qty", draft), "배추 몇 박스이에요?");
  assert.equal(clarificationQuestion("items[0].qty", createCandidateDraft({ ...snapshot, items: [{ ...snapshot.items[0], unit: null }] })), "배추 수량이 얼마예요?");
  for (const invalid of ["0", "100001", "2.345", "두 박스", ""]) {
    assert.ok(pendingClarifications(answerClarification(draft, "items[0].qty", invalid)).includes("items[0].qty"));
  }
  draft = answerClarification(draft, "items[0].qty", "2.5");
  for (const boundary of ["0.01", "100000"]) assert.ok(!pendingClarifications(answerClarification(draft, "items[0].qty", boundary)).includes("items[0].qty"));
  assert.equal(draft.items[0].amount, "");
  assert.equal(draft.items[0].unit_price, "");
  for (const invalid of ["0", "100000001", "1.5", "만 원"]) {
    assert.ok(pendingClarifications(answerClarification(draft, "total_amount", invalid)).includes("total_amount"));
  }
  draft = answerClarification(draft, "total_amount", "25000");
  for (const boundary of ["1", "100000000"]) assert.ok(!pendingClarifications(answerClarification(draft, "total_amount", boundary)).includes("total_amount"));
  draft = answerClarification(draft, "counterparty", "테스트상사");
  assert.deepEqual(pendingClarifications(draft), []);
  assert.equal(draft.candidateId, null);
  assert.equal(draft.credit, false);
  const extracted = createCandidateDraft(snapshot);
  assert.equal(answerClarification(extracted, "items[0].qty", "3").items[0].amount, "1000");
  assert.equal(answerClarification(extracted, "credit", "외상"), extracted);
  assert.equal(answerClarification(extracted, "total_amount", true), extracted);
  const multiple = { ...draft, items: [{ ...draft.items[0], qty: "" }, { ...draft.items[0], qty: "" }] };
  assert.deepEqual(pendingClarifications(answerClarification(multiple, "items[0].qty", "2")), ["items[1].qty"]);
});

test("DL-31 card shows one question and transcript, with a full form escape and no automatic submission", () => {
  const draft = createCandidateDraft({ ...snapshot, credit: null });
  const props = { message: "빠진 정보를 채워 주세요", draft, busy: false, submitError: null, problemFields: ["credit"],
    onChange() {}, onSubmit() {}, onClose() {} };
  const markup = renderToStaticMarkup(<RepairDialog {...props} errorCode="MISSING_REQUIRED_FIELD" transcript="테스트상사 배추 두 개 천 원" />);
  assert.match(markup, /외상이에요, 현금이에요/);
  assert.match(markup, /이렇게 들렸어요/);
  assert.match(markup, /다른 내용도 고치기/);
  assert.doesNotMatch(markup, /<input|수정 내용 제출/);
  const fallback = renderToStaticMarkup(<RepairDialog {...props} errorCode="AMOUNT_MISMATCH" />);
  assert.match(fallback, /<input/);
  assert.match(fallback, /수정 내용 제출/);
  const quantityDraft = { ...draft, credit: false, items: [{ ...draft.items[0], qty: "0" }] };
  const quantityMarkup = renderToStaticMarkup(<RepairDialog {...props} draft={quantityDraft} busy errorCode="MISSING_REQUIRED_FIELD" />);
  assert.match(quantityMarkup, /aria-invalid="true"/);
  assert.match(quantityMarkup, /role="status"/);
  assert.match(quantityMarkup, /fieldset[^>]*disabled/);
  assert.doesNotMatch(quantityMarkup, /수정 내용 제출/);
  const unnamed = renderToStaticMarkup(<RepairDialog {...props} draft={{ ...draft, items: [{ ...draft.items[0], name: "" }] }} errorCode="MISSING_REQUIRED_FIELD" />);
  assert.match(unnamed, /품목명/);
  assert.match(unnamed, /수정 내용 제출/);
});

test("arithmetic repair requires an explicit amount choice before proceeding", () => {
  const draft = createCandidateDraft({ ...snapshot, total_amount: 2000 });
  const markup = renderToStaticMarkup(<RepairDialog message="합계가 달라요" errorCode="AMOUNT_MISMATCH"
    draft={draft} busy={false} submitError={null} problemFields={["total_amount"]}
    onChange={() => {}} onSubmit={() => {}} onClose={() => {}} />);
  assert.match(markup, /합계가 2,000원이에요, 1,000원이에요/);
  assert.match(markup, /<button[^>]*disabled=""[^>]*>입력 내용 확인하기<\/button>/);
  assert.doesNotMatch(markup, /수정 내용 제출/);
});

test("unsupported transactions show guidance without offering partial candidate repair", () => {
  for (const transcript of [undefined, "판매 삼만원 중 만원 받았어"]) {
    const config = { headers: new axios.AxiosHeaders() };
    const error = new AxiosError("unsupported", undefined, config, undefined, {
      status: 400, statusText: "Bad Request", headers: {}, config,
      data: { error_code: "UNSUPPORTED_TRANSACTION", message: "incorrect generic repair prompt",
        partial_candidate: snapshot, problem_fields: ["credit"], utterance_text: transcript },
    });
    const failure = describeApiFailure(error);
    assert.equal(failure.kind, "input");
    assert.equal(failure.errorCode, "UNSUPPORTED_TRANSACTION");
    assert.equal(failure.partialCandidate, undefined);
    assert.deepEqual(failure.problemFields, []);
    assert.equal(failure.utteranceText, transcript);
    assert.match(failure.message, /판매·외상·입금/);
    const markup = renderToStaticMarkup(<ErrorDialog title="이 거래는 기록할 수 없어요" message={failure.message}
      hint="부분 결제는 판매를 먼저 외상으로 기록하고, 받은 돈은 입금으로 등록해 주세요."
      transcript={transcript} actionLabel="다시 입력하기" onClose={() => {}} />);
    assert.match(markup, /role="alertdialog"/);
    assert.match(markup, /입금으로 등록/);
    assert.doesNotMatch(markup, /<input|수정 내용 제출/);
    if (transcript) assert.match(markup, /이렇게 들렸어요/);
  }
});

test("draft edits survive re-entry only for their original candidate", () => {
  const draft = createCandidateDraft(snapshot, snapshot.entry_id);
  saveCandidateDraft({ ...draft, counterparty: "수정한 거래처" });
  assert.equal(restoreCandidateDraft(snapshot).counterparty, "수정한 거래처");
  assert.equal(restoreCandidateDraft({ ...snapshot, entry_id: "other" }).counterparty, "테스트상사");
  assert.equal(draftToCandidateFields(draft).items[0].unit_price, null);
  const priced = updateDraftItem(draft, 0, "unit_price", "750");
  assert.equal(priced.total_amount, "1500");
});

test("candidate restoration rechecks server status and clears confirmed drafts", async () => {
  useCandidateStore.getState().setCandidate(snapshot);
  saveCandidateDraft(createCandidateDraft(snapshot, snapshot.entry_id));
  assert.deepEqual(JSON.parse(localStorage.getItem("echobill_last_candidate")!), { entry_id: snapshot.entry_id });
  api.defaults.adapter = async (config) => {
    assert.equal(config.url, "/api/v1/ledger/draft-entry");
    return { status: 200, statusText: "OK", data: { ...snapshot, status: "confirmed" }, headers: {}, config };
  };
  await useCandidateStore.getState().hydrateFromStorage();
  assert.equal(useCandidateStore.getState().candidate, null);
  assert.equal(localStorage.getItem("echobill_candidate_draft"), null);
});

test("offline restoration preserves the pointer and unsaved edits for retry", async () => {
  useCandidateStore.getState().setCandidate(snapshot);
  const draft = { ...createCandidateDraft(snapshot, snapshot.entry_id), counterparty: "수정" };
  saveCandidateDraft(draft);
  api.defaults.adapter = async () => { throw new AxiosError("offline"); };
  await useCandidateStore.getState().hydrateFromStorage();
  assert.ok(localStorage.getItem("echobill_last_candidate"));
  assert.equal(restoreCandidateDraft(snapshot).counterparty, "수정");
  assert.match(useCandidateStore.getState().restoreError!, /연결/);
});

test("late restoration cannot overwrite a newer parsed candidate", async () => {
  useCandidateStore.getState().setCandidate(snapshot);
  let release!: () => void;
  const pending = new Promise<void>((resolve) => { release = resolve; });
  api.defaults.adapter = async (config) => {
    await pending;
    return { status: 200, statusText: "OK", data: { ...snapshot, status: "candidate" }, headers: {}, config };
  };
  const restoring = useCandidateStore.getState().hydrateFromStorage();
  useCandidateStore.getState().setCandidate({ ...snapshot, entry_id: "new-entry" });
  release();
  await restoring;
  assert.equal(useCandidateStore.getState().candidate?.entry_id, "new-entry");
});
function failure(status: number, error_code: string, headers: Record<string, string> = {}) {
  return new AxiosError("request rejected", undefined, undefined, undefined,
    { status, data: { error_code }, headers, config: { headers: {} } } as never);
}

test("voice validation 400 preserves the optional transcript in either error envelope", () => {
  const transcript = "  시연상사 배추 두 박스 외상  ";
  for (const data of [{ error_code: "VALIDATION_ERROR", utterance_text: transcript },
    { detail: { error_code: "VALIDATION_ERROR", utterance_text: transcript } }]) {
    const error = new AxiosError("rejected", undefined, undefined, undefined,
      { status: 400, data, headers: {}, config: { headers: {} } } as never);
    assert.equal(describeApiFailure(error).utteranceText, transcript);
    assert.equal(describeApiFailure(error).kind, "input");
  }
});

test("missing or invalid transcripts and non-STT failures do not supply replacement text", () => {
  const cases = [
    { status: 400, error_code: "VALIDATION_ERROR", utterance_text: undefined },
    { status: 400, error_code: "VALIDATION_ERROR", utterance_text: null },
    { status: 400, error_code: "VALIDATION_ERROR", utterance_text: 123 },
    { status: 400, error_code: "VALIDATION_ERROR", utterance_text: "   " },
    { status: 400, error_code: "AUDIO_INVALID", utterance_text: "시연 문장" },
    { status: 422, error_code: "VALIDATION_ERROR", utterance_text: "시연 문장" },
    { status: 504, error_code: "STT_TIMEOUT", utterance_text: "시연 문장" },
  ];
  for (const { status, ...data } of cases) {
    const error = new AxiosError("rejected", undefined, undefined, undefined,
      { status, data, headers: {}, config: { headers: {} } } as never);
    assert.equal(describeApiFailure(error).utteranceText, undefined);
  }
});


test("lost payment response retries the same key, then a new explicit payment gets a new key", async () => {
  const keys: string[] = [];
  let lost = true;
  api.defaults.adapter = async (config) => {
    keys.push(String(config.headers["X-Idempotency-Key"]));
    if (lost) { lost = false; throw new AxiosError("lost response"); }
    return { status: 200, statusText: "OK", data: {}, headers: {}, config };
  };
  await assert.rejects(recordPayment("테스트상사", 10000));
  await recordPayment("테스트상사", 10000);
  await recordPayment("테스트상사", 10000);
  assert.equal(keys[0], keys[1]);
  assert.notEqual(keys[1], keys[2]);
});

test("reviewing settlement for new input releases only the abandoned payment key", async () => {
  const keys: string[] = [];
  api.defaults.adapter = async (config) => {
    if (config.method === "get") return { status: 200, statusText: "OK", data: { counterparty: "테스트상사", payments: [] }, headers: {}, config };
    keys.push(String(config.headers["X-Idempotency-Key"]));
    throw new AxiosError("lost response");
  };
  await assert.rejects(recordPayment("테스트상사", 10000));
  await assert.rejects(recordPayment("다른상사", 10000));
  const detail = await reviewPaymentForNewInput("테스트상사", 10000);
  assert.equal(detail.counterparty, "테스트상사");
  await assert.rejects(recordPayment("테스트상사", 10000));
  await assert.rejects(recordPayment("다른상사", 10000));
  assert.notEqual(keys[0], keys[2]);
  assert.equal(keys[1], keys[3]);
});

test("a failed settlement review preserves the payment key for replay", async () => {
  const keys: string[] = [];
  api.defaults.adapter = async (config) => {
    if (config.method !== "get") keys.push(String(config.headers["X-Idempotency-Key"]));
    throw new AxiosError("lost response");
  };
  await assert.rejects(recordPayment("테스트상사", 10000));
  await assert.rejects(reviewPaymentForNewInput("테스트상사", 10000));
  await assert.rejects(recordPayment("테스트상사", 10000));
  assert.equal(keys[0], keys[1]);
});

test("an uncertain payment remains recognizable after re-entry and late completion cannot erase a new attempt", async () => {
  const old = await retryKey("payment", { counterparty: "테스트상사", amount: 1000 });
  assert.equal(old.reused, false);
  const restored = await retryKey("payment", { counterparty: "테스트상사", amount: 1000 });
  assert.equal(restored.reused, true);
  assert.equal(restored.value, old.value);
  releaseRetryAttempt(old);
  const next = await retryKey("payment", { counterparty: "테스트상사", amount: 1000 });
  assert.notEqual(old.value, next.value);
  releaseRetryAttempt(old);
  assert.equal(sessionStorage.getItem(next.storageKey), next.value);
});

test("an in-progress payment offers only replay, not a separate new payment", () => {
  const html = renderToStaticMarkup(<PaymentView balances={[{ counterparty: "테스트상사", unpaid: 20000 }]}
    counterparty="테스트상사" amount="1000" uncertain onCounterparty={() => {}} onAmount={() => {}} onBack={() => {}} onSubmit={() => {}} />);
  assert.match(html, /처리 결과 다시 확인/);
  assert.doesNotMatch(html, /정산 확인하고 새로 입력/);
});

test("pending payment state survives UI dismissal and re-entry until its request completes", async () => {
  api.defaults.adapter = async () => { throw failure(409, "IDEMPOTENCY_IN_PROGRESS"); };
  await assert.rejects(recordPayment("테스트상사", 1000));
  const restored = await retryKey("payment", { counterparty: "테스트상사", amount: 1000 });
  assert.equal(restored.pending, true);
  api.defaults.adapter = async (config) => ({ status: 200, statusText: "OK", data: {}, headers: {}, config });
  await recordPayment("테스트상사", 1000, restored);
  assert.equal(sessionStorage.getItem(`${restored.storageKey}_pending`), null);
  assert.equal((await retryKey("payment", { counterparty: "테스트상사", amount: 1000 })).pending, false);
});

test("pending feedback retains the key while an input rejection releases it", async () => {
  const keys: string[] = [];
  const statuses = [409, 400, 200];
  api.defaults.adapter = async (config) => {
    keys.push(String(config.headers["X-Idempotency-Key"]));
    const status = statuses.shift()!;
    if (status !== 200) throw failure(status, status === 409 ? "IDEMPOTENCY_IN_PROGRESS" : "AMOUNT_MISMATCH");
    return { status, statusText: "OK", data: {}, headers: {}, config };
  };
  await assert.rejects(sendFeedback("entry", { action: "confirm" }));
  await assert.rejects(sendFeedback("entry", { action: "confirm" }));
  await sendFeedback("entry", { action: "confirm" });
  assert.equal(keys[0], keys[1]);
  assert.notEqual(keys[1], keys[2]);
});

test("parse normalizes optional fields and never changes candidate to confirmed", async () => {
  api.defaults.adapter = async (config) => {
    assert.equal(config.url, "/api/v1/ledger/parse");
    assert.equal(config.timeout, 15000);
    return { status: 200, statusText: "OK", headers: {}, config,
      data: { entry_id: "candidate", status: "candidate", utterance_text: "테스트", items: [{ name: "배추" }] } };
  };
  const candidate = await parseUtterance("테스트");
  assert.equal(candidate.status, "candidate");
  assert.equal(candidate.items[0].qty, null);
  assert.equal(candidate.items[0].unit_price, null);
});


test("auth and network errors render different recovery actions", () => {
  const auth = renderToStaticMarkup(<StaticRouter location="/ledger"><RequestError failure={describeApiFailure(failure(401, "UNAUTHORIZED"))} retry={() => {}} /></StaticRouter>);
  const network = renderToStaticMarkup(<StaticRouter location="/ledger"><RequestError failure={describeApiFailure(new AxiosError("offline"))} retry={() => {}} /></StaticRouter>);
  assert.match(auth, /href="\/login"/);
  assert.doesNotMatch(auth, /다시 불러오기/);
  assert.match(network, /인터넷 연결을 확인해 주세요/);
  assert.match(network, /다시 시도/);
  assert.doesNotMatch(network, /href="\/login"/);
  assert.equal(describeApiFailure(failure(429, "RATE_LIMITED")).kind, "rate");
});

test("HTTP notices distinguish expired, conflict, unconfirmed, pending and rate limit", () => {
  for (const [status, code, kind] of [[404, "NOT_FOUND", "notFound"], [409, "CANDIDATE_EXPIRED", "expired"],
    [409, "IDEMPOTENCY_CONFLICT", "conflict"], [409, "ENTRY_NOT_CONFIRMED", "notConfirmed"],
    [409, "IDEMPOTENCY_IN_PROGRESS", null], [413, "PAYLOAD_TOO_LARGE", "tooLong"],
    [429, "RATE_LIMITED", "rate"], [500, "INTERNAL_ERROR", "server"]] as const) {
    assert.equal(errorNoticeKind(describeApiFailure(failure(status, code))), kind);
  }
  const limited = describeApiFailure(failure(429, "RATE_LIMITED", { "retry-after": "12" }));
  const html = renderToStaticMarkup(<StaticRouter><RequestError failure={limited} retry={() => {}} /></StaticRouter>);
  assert.match(html, /12초/);
  assert.match(html, /disabled=""/);
  assert.match(describeApiFailure(failure(409, "ENTRY_NOT_CONFIRMED")).message, /장부를 먼저 확정/);
});

test("drafts expire at seven days and legacy or future timestamps are not restored", () => {
  const now = Date.now();
  writeDraft(sessionStorage, "echobill_utterance_draft", "유지할 입력", now);
  assert.equal(readDraft(sessionStorage, "echobill_utterance_draft", now + 7 * 86400000 - 1), "유지할 입력");
  assert.equal(readDraft(sessionStorage, "echobill_utterance_draft", now + 7 * 86400000), null);
  localStorage.setItem("echobill_candidate_draft", JSON.stringify({ saved_at: now + 1000, value: {} }));
  assert.equal(readDraft(localStorage, "echobill_candidate_draft", now), null);
  sessionStorage.setItem("echobill_utterance_draft", "legacy text");
  assert.equal(readDraft(sessionStorage, "echobill_utterance_draft", now), null);
});

test("logout clears draft, candidate and retry storage but preserves unrelated settings", () => {
  for (const target of [localStorage, sessionStorage]) {
    for (const key of ["echobill_utterance_draft", "echobill_candidate_draft", "echobill_last_candidate", "echobill_retry_payment_hash", "echobill_access_token", "echobill_user_id", "echobill_oauth_state"]) target.setItem(key, "value");
    target.setItem("unrelated", "keep");
  }
  clearSessionDrafts();
  assert.deepEqual(retryStorage.entries(), [["unrelated", "keep"]]);
  assert.equal(localStorage.getItem("echobill_last_candidate"), null);
  assert.equal(localStorage.getItem("unrelated"), "keep");
});

test("unsent text survives tab storage loss and is removed by logout or explicit clearing", () => {
  saveUtteranceDraft("웹뷰 종료 후 복원할 입력");
  sessionStorage.clear();
  assert.equal(readUtteranceDraft(), "웹뷰 종료 후 복원할 입력");
  clearSessionDrafts();
  assert.equal(readUtteranceDraft(), "");
  saveUtteranceDraft("확정 뒤 제거할 입력");
  saveUtteranceDraft("");
  assert.equal(readUtteranceDraft(), "");
});

test("tab draft migration keeps the original expiry and does not replace a local draft", () => {
  const now = Date.now();
  writeDraft(sessionStorage, "echobill_utterance_draft", "이전 탭 입력", now - 86400000);
  assert.equal(readUtteranceDraft(), "이전 탭 입력");
  assert.equal(JSON.parse(localStorage.getItem("echobill_utterance_draft")!).saved_at, now - 86400000);
  assert.equal(sessionStorage.getItem("echobill_utterance_draft"), null);
  saveUtteranceDraft("이전 탭 입력");
  assert.equal(JSON.parse(localStorage.getItem("echobill_utterance_draft")!).saved_at, now - 86400000);
  saveUtteranceDraft("새 로컬 입력");
  writeDraft(sessionStorage, "echobill_utterance_draft", "오래된 탭 입력");
  assert.equal(readUtteranceDraft(), "새 로컬 입력");
  writeDraft(localStorage, "echobill_utterance_draft", "만료 입력", now - 7 * 86400000);
  assert.equal(readUtteranceDraft(), "");
  writeDraft(sessionStorage, "echobill_utterance_draft", "유효한 탭 입력");
  localStorage.setItem("echobill_utterance_draft", "broken");
  assert.equal(readUtteranceDraft(), "유효한 탭 입력");
});

test("OAuth accepts only the matching single-use state and code", () => {
  const url = new URL(createLoginUrl("public-app-key", "http://localhost:5173/auth/kakao/callback"));
  const state = url.searchParams.get("state")!;
  assert.equal(consumeLoginCallback(`?state=${state}&code=valid`), "valid");
  assert.equal(consumeLoginCallback(`?state=${state}&code=valid`), null);
  createLoginUrl("public-app-key", "http://localhost:5173/auth/kakao/callback");
  assert.equal(consumeLoginCallback("?state=wrong&code=valid"), null);
  createLoginUrl("public-app-key", "http://localhost:5173/auth/kakao/callback");
  assert.equal(consumeLoginCallback(`?state=${sessionStorage.getItem("echobill_oauth_state")}&error=access_denied`), null);
});

test("session restoration uses the backend; outages preserve identity and 401 clears it", async () => {
  api.defaults.adapter = async (config) => ({ status: 200, statusText: "OK", data: { user_id: "cookie-user", expires_in_hours: 24 }, headers: {}, config });
  await useAuthStore.getState().hydrateFromStorage();
  assert.equal(useAuthStore.getState().userId, "cookie-user");
  assert.equal(localStorage.getItem("echobill_access_token"), null);
  api.defaults.adapter = async () => { throw new AxiosError("offline"); };
  await useAuthStore.getState().hydrateFromStorage();
  assert.equal(useAuthStore.getState().userId, "cookie-user");
  assert.equal(useAuthStore.getState().sessionError?.kind, "network");
  api.defaults.adapter = async () => { throw failure(401, "UNAUTHORIZED"); };
  await useAuthStore.getState().hydrateFromStorage();
  assert.equal(useAuthStore.getState().userId, null);
});

test("logout invalidates a late session restoration response", async () => {
  let release!: () => void;
  const wait = new Promise<void>((resolve) => { release = resolve; });
  api.defaults.adapter = async (config) => { await wait; return { status: 200, statusText: "OK", data: { user_id: "old-user" }, headers: {}, config }; };
  const pending = useAuthStore.getState().hydrateFromStorage();
  useAuthStore.getState().setCookieSession(null);
  release(); await pending;
  assert.equal(useAuthStore.getState().userId, null);
});

test("a late 401 cannot expire a newly logged-in session", async () => {
  let release!: () => void;
  let sent!: () => void;
  const pendingResponse = new Promise<void>((resolve) => { release = resolve; });
  const dispatched = new Promise<void>((resolve) => { sent = resolve; });
  let expired = 0;
  const count = () => { expired += 1; };
  window.addEventListener("echobill:session-expired", count);
  api.defaults.adapter = async (config) => {
    sent(); await pendingResponse;
    throw new AxiosError("expired", "ERR_BAD_REQUEST", config, undefined, { status: 401, statusText: "Unauthorized", data: {}, headers: {}, config });
  };
  const request = api.get("/api/v1/ledger");
  await dispatched;
  useAuthStore.getState().setCookieSession("new-login");
  release(); await assert.rejects(request);
  assert.equal(expired, 0);
  await assert.rejects(api.get("/api/v1/ledger"));
  assert.equal(expired, 1);
  window.removeEventListener("echobill:session-expired", count);
});

test("a different cookie user after expiry cannot restore the previous user's draft", () => {
  let cleared = 0;
  const resetInput = () => { cleared += 1; };
  window.addEventListener("echobill:session-ended", resetInput);
  useAuthStore.getState().setCookieSession("first-owner");
  saveUtteranceDraft("이전 사용자 입력");
  useAuthStore.getState().setCookieSession(null);
  useAuthStore.getState().setCookieSession("second-owner");
  assert.equal(readUtteranceDraft(), "");
  assert.equal(localStorage.getItem("echobill_draft_owner"), "second-owner");
  assert.equal(cleared, 1);
  saveUtteranceDraft("두 번째 사용자 입력");
  useAuthStore.getState().setCookieSession("third-owner");
  assert.equal(cleared, 2);
  assert.equal(readUtteranceDraft(), "");
  window.removeEventListener("echobill:session-ended", resetInput);
});

test("a missing payment choice renders two explicit choices, while calendar dots preserve credit occurrence", () => {
  const markup = renderToStaticMarkup(<ConfirmView candidate={candidateView(createCandidateDraft({ ...snapshot, credit: null }), "테스트", "등록일")}
    onBack={() => {}} onAnswer={() => {}} onCreditChange={() => {}} onEditItem={() => {}} onEdit={() => {}} onDiscard={() => {}} onConfirm={() => {}} />);
  assert.equal((markup.match(/aria-pressed="false"/g) ?? []).length, 2);
  assert.match(markup, /결제 상태/);
  assert.match(markup, /미수금/); assert.match(markup, /결제 완료/);
  assert.doesNotMatch(markup, /↔/);
  const rows = [{ ...snapshot, status: "confirmed", kst_date: "2026-10-06", created_at: "2026-10-06T00:00:00Z" },
    { ...snapshot, entry_id: "paid", credit: false, status: "confirmed", kst_date: "2026-10-07", created_at: "2026-10-07T00:00:00Z" }] as LedgerList["entries"];
  assert.deepEqual(calendarMarks(rows), { 6: { unpaid: true }, 7: { paid: true } });
  assert.deepEqual(calendarMarks([...rows, { ...rows[1], kst_date: "2026-10-06" }])[6], { unpaid: true, paid: true });
  assert.match(kstRegistrationLabel("2026-10-05T15:00:00"), /2026\. 10\. 06/);
});

test("audio returns a candidate with a separate timeout and replays a lost response with the same key", async () => {
  const keys: string[] = [];
  const audio = new Blob(["dummy audio"], { type: "audio/webm;codecs=opus" });
  api.defaults.adapter = async (config) => {
    assert.equal(config.url, "/api/v1/ledger/parse-audio");
    assert.equal(config.timeout, 25000);
    assert.equal(api.defaults.timeout, 15000);
    assert.equal(config.withCredentials, true);
    assert.ok(config.data instanceof FormData);
    keys.push(String(config.headers["X-Idempotency-Key"]));
    if (keys.length === 1) throw new AxiosError("lost response");
    return { status: 200, statusText: "OK", headers: {}, config, data: { ...snapshot, parser_type: "rule" } };
  };
  await assert.rejects(parseRecording(audio));
  const candidate = await parseRecording(audio);
  assert.equal(candidate.entry_id, snapshot.entry_id);
  assert.equal(candidate.credit, true);
  assert.equal(candidate.items[0].unit_price, null);
  assert.equal(keys[0], keys[1]);
});


test("retry storage contains hashes rather than customer data", async () => {
  let configKey = "";
  api.defaults.adapter = async (config) => { configKey = String(config.headers["X-Idempotency-Key"]); throw new AxiosError("offline"); };
  await assert.rejects(postOnce("payment", "/test", { counterparty: "테스트상사", amount: 1000 }));
  assert.match(configKey, /^[\da-f-]{36}$/);
  const entries = retryStorage.entries();
  assert.equal(entries.length, 1);
  assert.match(entries[0][0], /^echobill_retry_payment_[a-f\d]{64}$/);
  assert.doesNotMatch(JSON.stringify(entries), /테스트상사/);
  assert.ok(axios.isAxiosError(failure(400, "INVALID")));
});

test("401 and 429 retain uncertain request keys across login and throttling", async () => {
  const keys: string[] = [];
  const statuses = [401, 429, 200];
  api.defaults.adapter = async (config) => {
    keys.push(String(config.headers["X-Idempotency-Key"]));
    const status = statuses.shift()!;
    if (status !== 200) throw failure(status, "BLOCKED_BEFORE_HANDLER");
    return { status, statusText: "OK", data: {}, headers: {}, config };
  };
  await assert.rejects(recordPayment("테스트상사", 1000));
  await assert.rejects(recordPayment("테스트상사", 1000));
  await recordPayment("테스트상사", 1000);
  assert.equal(new Set(keys).size, 1);
});

test("blocked browser storage keeps drafts and lost payment retry keys in memory and clears on logout", async () => {
  const local = Object.getOwnPropertyDescriptor(globalThis, "localStorage")!;
  const session = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage")!;
  const originalAdapter = api.defaults.adapter;
  const blocked = () => { throw new DOMException("storage blocked", "SecurityError"); };
  try {
    Object.defineProperty(globalThis, "localStorage", { configurable: true, get: blocked });
    Object.defineProperty(globalThis, "sessionStorage", { configurable: true, get: blocked });
    assert.equal(readUtteranceDraft(), "");
    saveUtteranceDraft("저장 제한 중 작성한 내용");
    assert.equal(readUtteranceDraft(), "저장 제한 중 작성한 내용");
    useAuthStore.getState().setCookieSession("storage-test");
    useCandidateStore.getState().setCandidate(snapshot);
    assert.equal(useCandidateStore.getState().candidate?.entry_id, snapshot.entry_id);
    const keys: string[] = [];
    api.defaults.adapter = async (config) => {
      keys.push(String(config.headers["X-Idempotency-Key"]));
      if (keys.length === 1) throw new AxiosError("response lost", undefined, config);
      return { data: {}, status: 200, statusText: "OK", headers: {}, config };
    };
    await assert.rejects(recordPayment("저장제한상사", 1000));
    await recordPayment("저장제한상사", 1000);
    assert.equal(keys[0], keys[1]);
    clearSessionDrafts();
    assert.equal(readUtteranceDraft(), "");
    assert.equal(localStore.getItem("echobill_last_candidate"), null);
    assert.equal(sessionStore.length, 0);
    assert.throws(() => createLoginUrl("client", "https://app.echobill.kr/auth/kakao/callback"), /OAUTH_STORAGE_UNAVAILABLE/);
  } finally {
    Object.defineProperty(globalThis, "localStorage", local);
    Object.defineProperty(globalThis, "sessionStorage", session);
    api.defaults.adapter = originalAdapter;
    localStore.clear(); sessionStore.clear();
  }
});

test("storage cleanup failures do not turn an acknowledged payment into a failed payment", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage")!;
  const originalAdapter = api.defaults.adapter;
  const backing = storage();
  try {
    Object.defineProperty(globalThis, "sessionStorage", { configurable: true, value: {
      ...backing, removeItem: () => { throw new DOMException("storage blocked", "SecurityError"); },
    } });
    api.defaults.adapter = async (config) => ({ data: { accepted: true }, status: 200, statusText: "OK", headers: {}, config });
    assert.deepEqual(await recordPayment("응답확인상사", 1000), { accepted: true });
    // The rejected remove must hide the stale physical key from subsequent reads.
    const key = backing.key(0)!;
    assert.ok(key); assert.equal(sessionStore.getItem(key), null);
  } finally {
    Object.defineProperty(globalThis, "sessionStorage", descriptor);
    api.defaults.adapter = originalAdapter;
    sessionStore.clear();
  }
});

test("cached draft values are cleared even if storage becomes unreadable after a successful save", () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "localStorage")!;
  try {
    saveUtteranceDraft("세션 중 저장한 초안");
    Object.defineProperty(globalThis, "localStorage", { configurable: true, get: () => { throw new DOMException("denied", "SecurityError"); } });
    assert.equal(readUtteranceDraft(), "세션 중 저장한 초안");
    clearSessionDrafts();
    assert.equal(readUtteranceDraft(), "");
  } finally { Object.defineProperty(globalThis, "localStorage", descriptor); localStore.clear(); }
});

test("OAuth can restart when persistent storage recovers after an earlier restriction", () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "sessionStorage")!;
  try {
    Object.defineProperty(globalThis, "sessionStorage", { configurable: true, get: () => { throw new DOMException("denied", "SecurityError"); } });
    assert.throws(() => createLoginUrl("client", "https://app.echobill.kr/auth/kakao/callback"), /OAUTH_STORAGE_UNAVAILABLE/);
    Object.defineProperty(globalThis, "sessionStorage", descriptor);
    const login = new URL(createLoginUrl("client", "https://app.echobill.kr/auth/kakao/callback"));
    assert.equal(consumeLoginCallback(`?state=${login.searchParams.get("state")}&code=recovered-code`), "recovered-code");
  } finally { Object.defineProperty(globalThis, "sessionStorage", descriptor); sessionStore.clear(); }
});

test("a delayed voice candidate cannot replace a newer candidate or survive explicit discard", () => {
  useCandidateStore.getState().setCandidate(null);
  const oldResponse = guardCandidateResponse();
  assert.equal(oldResponse(), true);
  useCandidateStore.getState().setCandidate(snapshot);
  assert.equal(oldResponse(), false);
  const discardedResponse = guardCandidateResponse();
  useCandidateStore.getState().setCandidate(null);
  assert.equal(discardedResponse(), false);
});

test("an unchanged candidate still permits background voice recovery", () => {
  useCandidateStore.getState().setCandidate(null);
  const response = guardCandidateResponse();
  assert.equal(response(), true);
});

test("changing accounts remounts protected page state before another account can use it", async () => {
  const previous = api.defaults.adapter;
  const previousDocument = Object.getOwnPropertyDescriptor(globalThis, "document");
  const previousWindow = Object.getOwnPropertyDescriptor(globalThis, "window")!;
  let root: ReactTestRenderer | undefined;
  function AccountPage() {
    const [privateValue] = useState(() => useAuthStore.getState().userId);
    return <p id="private-value">{privateValue}</p>;
  }
  const browserWindow = Object.assign(new EventTarget(), { setTimeout, clearTimeout, scrollTo() {}, location: new URL("http://localhost/account") });
  Object.defineProperty(globalThis, "window", { configurable: true, writable: true, value: browserWindow });
  Object.defineProperty(globalThis, "document", { configurable: true, value: new EventTarget() });
  useAuthStore.getState().setCookieSession("first-account");
  useCandidateStore.getState().setCandidate(null);
  api.defaults.adapter = async (config) => ({ config, status: 200, statusText: "OK", headers: {}, data: { user_id: "first-account" } });
  const router = createMemoryRouter([{ element: <DesignShell />, children: [{ path: "/account", element: <AccountPage /> }] }], { initialEntries: ["/account"] });
  try {
    await act(async () => { root = create(<RouterProvider router={router} />); });
    assert.deepEqual(root!.root.findByProps({ id: "private-value" }).children, ["first-account"]);
    await act(async () => { useAuthStore.getState().setCookieSession("second-account"); });
    assert.deepEqual(root!.root.findByProps({ id: "private-value" }).children, ["second-account"],
      "old account form values and modal state must not remain mounted after switching accounts");
  } finally {
    await act(async () => { root?.unmount(); }); router.dispose(); api.defaults.adapter = previous;
    Object.defineProperty(globalThis, "window", previousWindow);
    if (previousDocument) Object.defineProperty(globalThis, "document", previousDocument);
    else Reflect.deleteProperty(globalThis, "document");
  }
});

for (const operation of ["text", "repair", "audio", "answer", "payment-review", "queued-post"] as const) {
  test(`account change cancels ${operation} before another account receives the request`, async () => {
    const previous = api.defaults.adapter;
    let sent = 0;
    useAuthStore.getState().setCookieSession("request-owner");
    api.defaults.adapter = async (config) => {
      sent++;
      return { config, status: 200, statusText: "OK", headers: {}, data: { ...snapshot, parser_type: "manual" } };
    };
    try {
      const audio = new Blob(["synthetic audio"], { type: "audio/webm" });
      const pending = operation === "text" ? parseUtterance("첫 계정의 거래 내용")
        : operation === "repair" ? parseCorrectedCandidate("첫 계정의 보완", snapshot, "previous-failure")
        : operation === "audio" ? parseRecording(audio)
        : operation === "answer" ? submitVoiceAnswer("total", audio)
        : operation === "payment-review" ? reviewPaymentForNewInput("첫 계정 거래처", 1000)
        : api.post("/api/v1/tax/business", { business_number: "1234567890" });
      useAuthStore.getState().setCookieSession("next-owner");
      let cancelled: unknown;
      await pending.catch((error: unknown) => { cancelled = error; });
      assert.equal(sent, 0, "session changes must prevent dispatch, not merely hide the late response");
      assert.ok(axios.isCancel(cancelled));
      assert.equal([...Array(sessionStore.length)].map((_, index) => sessionStore.key(index)).some((key) => key?.startsWith("echobill_retry_")), false);
    } finally { api.defaults.adapter = previous; }
  });
}

for (const operation of ["audio", "answer"] as const) {
  test(`late ${operation} response cannot erase the next account's retry key`, async () => {
    const previous = api.defaults.adapter;
    let release!: () => void;
    let dispatched!: () => void;
    const started = new Promise<void>((resolve) => { dispatched = resolve; });
    const response = new Promise<void>((resolve) => { release = resolve; });
    useAuthStore.getState().setCookieSession("old-audio-owner");
    api.defaults.adapter = async (config) => {
      dispatched(); await response;
      return { config, status: 200, statusText: "OK", headers: {}, data: operation === "audio"
        ? { ...snapshot, parser_type: "rule" } : { field: "total", status: "ok", value: "15000", unit: null } };
    };
    try {
      const audio = new Blob(["synthetic repeated audio"], { type: "audio/webm" });
      const pending = operation === "audio" ? parseRecording(audio) : submitVoiceAnswer("total", audio);
      await started;
      const storageKey = [...Array(sessionStore.length)].map((_, index) => sessionStore.key(index)).find((key) => key?.startsWith("echobill_retry_"))!;
      assert.ok(storageKey);
      useAuthStore.getState().setCookieSession("new-audio-owner");
      sessionStore.setItem(storageKey, "new-owners-independent-attempt");
      release(); await pending;
      assert.equal(sessionStore.getItem(storageKey), "new-owners-independent-attempt");
    } finally { release(); api.defaults.adapter = previous; }
  });
}

for (const sessionResult of ["changed", "expired"] as const) {
  test(`session refresh ${sessionResult} also cancels old audio preparation`, async () => {
    const previous = api.defaults.adapter;
    let release!: () => void;
    const bytesReady = new Promise<void>((resolve) => { release = resolve; });
    let sent = 0;
    useAuthStore.getState().setCookieSession("previous-owner");
    api.defaults.adapter = async (config) => {
      if (config.url?.endsWith("/auth/me")) {
        if (sessionResult === "expired") throw failure(401, "UNAUTHORIZED");
        return { config, status: 200, statusText: "OK", headers: {}, data: { user_id: "refreshed-owner" } };
      }
      sent++;
      return { config, status: 200, statusText: "OK", headers: {}, data: { ...snapshot, parser_type: "rule" } };
    };
    try {
      const audio = new Blob(["synthetic blocked preparation"], { type: "audio/webm" });
      const originalBytes = audio.arrayBuffer.bind(audio);
      audio.arrayBuffer = async () => { await bytesReady; return originalBytes(); };
      const pending = parseRecording(audio);
      await useAuthStore.getState().hydrateFromStorage();
      assert.equal(useAuthStore.getState().userId, sessionResult === "changed" ? "refreshed-owner" : null);
      release();
      let cancelled: unknown;
      await pending.catch((error: unknown) => { cancelled = error; });
      assert.equal(sent, 0);
      assert.ok(axios.isCancel(cancelled));
    } finally { release(); api.defaults.adapter = previous; }
  });
}

test("multiple counterparties from the API show retry guidance for text and voice", () => {
  for (const fromVoice of [false, true]) {
    const result = describeApiFailure(failure(400, "MULTIPLE_COUNTERPARTIES_DETECTED"));
    const html = renderToStaticMarkup(<RetryNotice code={result.errorCode as Parameters<typeof RetryNotice>[0]["code"]}
      heard="가게 하나와 가게 둘에 납품" fromVoice={fromVoice} onRetry={() => {}} onType={() => {}} onClose={() => {}} />);
    assert.match(html, /한 번에 한 곳만 적을 수 있어요/);
    assert.match(html, /거래처마다 따로 말해 주세요/);
    assert.match(html, /다시 말하기/);
    assert.match(html, fromVoice ? /이렇게 들렸어요/ : /적은 내용/);
  }
});

test("session retry after an initial outage restores the server candidate and preserves edits", async () => {
  const previous = api.defaults.adapter;
  try {
    localStore.setItem("echobill_last_candidate", JSON.stringify({ entry_id: snapshot.entry_id }));
    saveCandidateDraft({ ...createCandidateDraft(snapshot, snapshot.entry_id), counterparty: "저장한 수정" });
    useCandidateStore.setState({ candidate: null, hydrated: false, restoreError: null });
    api.defaults.adapter = async () => { throw new AxiosError("offline"); };
    await restoreAppSession();
    assert.equal(useAuthStore.getState().sessionError?.kind, "network");
    const requests: string[] = [];
    api.defaults.adapter = async (config) => {
      requests.push(config.url!);
      return { config, status: 200, statusText: "OK", headers: {}, data: config.url?.endsWith("/me")
        ? { user_id: "restored-owner", ai_consent: false }
        : { ...snapshot, status: "candidate" } };
    };
    await restoreAppSession();
    assert.deepEqual(requests, ["/api/v1/auth/me", "/api/v1/ledger/draft-entry"]);
    assert.equal(useAuthStore.getState().sessionError, null);
    assert.equal(useCandidateStore.getState().hydrated, true);
    assert.equal(useCandidateStore.getState().candidate?.entry_id, snapshot.entry_id);
    assert.equal(restoreCandidateDraft(snapshot).counterparty, "저장한 수정");
  } finally { api.defaults.adapter = previous; useCandidateStore.getState().setCandidate(null); }
});
