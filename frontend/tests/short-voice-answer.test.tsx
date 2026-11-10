import { RecordPage } from "../src/pages/RecordPage";
import { RepairDialog } from "../src/components/RepairDialog";
import { saveRepairDraft } from "../src/utils/repairStorage";
import { useCandidateStore } from "../src/stores/appStore";
import { test } from "node:test";
import assert from "node:assert/strict";
import { useState } from "react";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { AxiosError } from "axios";
import { api } from "../src/api/client";
import { snapshot, until } from "./review-fixture";
import { ClarificationCard } from "../src/components/ClarificationCard";
import { ClarifyCard } from "../src/ui/ClarifyCard";
import { ErrorNotice } from "../src/ui/ErrorNotice";
import { RecordingOverlay } from "../src/ui/RecordingOverlay";
import { useAuthStore } from "../src/stores/appStore";
import { createCandidateDraft } from "../src/utils/candidateDraft";
import { saveGuidanceSoundEnabled } from "../src/utils/guidanceAudio";
import { saveRecordingCueEnabled } from "../src/utils/recordingCue";

for (const offline of [false, true]) {
  test(`two taps record and send an answer; retries retain audio and question: offline=${offline}`, async () => {
    localStorage.clear(); sessionStorage.clear();
    saveGuidanceSoundEnabled(false); saveRecordingCueEnabled(false);
    useAuthStore.getState().setCookieSession("short-answer-owner", ["total"]);
    const previous = api.defaults.adapter;
    let microphones = 0, stopped = 0, uploads = 0;
    const keys: string[] = [];
    let rejectRetry: (() => void) | undefined;
    class Recorder {
      static isTypeSupported() { return true; }
      state = "inactive"; mimeType = "audio/webm";
      ondataavailable?: (event: { data: Blob }) => void;
      onstop?: () => void;
      start() { this.state = "recording"; }
      stop() { this.state = "inactive"; this.ondataavailable?.({ data: new Blob(["synthetic answer"]) }); this.onstop?.(); }
    }
    Object.assign(globalThis, { MediaRecorder: Recorder });
    Object.defineProperty(globalThis, "navigator", { configurable: true, value: { mediaDevices: {
      getUserMedia: async () => { microphones++; return { getTracks: () => [{ stop() { stopped++; } }] }; },
    } } });
    api.defaults.adapter = async config => {
      assert.ok(config.url?.endsWith("/voice-answer")); uploads++;
      keys.push(String(config.headers["X-Idempotency-Key"]));
      if (offline && uploads === 1) throw new AxiosError("offline");
      if (offline && uploads === 2) return new Promise((_resolve, reject) => { rejectRetry = () => reject(new AxiosError("still offline")); });
      return { config, status: 200, statusText: "OK", headers: {}, data: { field: "total", status: "ok", value: 30000, utterance_text: "삼만 원" } };
    };
    function Harness() {
      const [draft, setDraft] = useState({ ...createCandidateDraft(snapshot), total_amount: "" });
      return <ClarificationCard draft={draft} transcript="푸른마트 계란 다섯 판 외상" busy={false} onChange={setDraft} onFinish={() => {}} onClose={() => {}} />;
    }
    const router = createMemoryRouter([{ path: "/record", element: <Harness /> }], { initialEntries: ["/record"] });
    let root!: ReactTestRenderer;
    try {
      await act(async () => { root = create(<RouterProvider router={router} />); });
      assert.equal(microphones, 0);
      await act(async () => { root.root.findByType(ClarifyCard).props.onVoiceAnswer(); });
      assert.equal(microphones, 1);
      assert.equal(root.root.findAllByType(RecordingOverlay).length, 0);
      await act(async () => { root.root.findAllByType("button").find(b => b.children.includes("녹음 종료"))!.props.onClick(); });
      await act(async () => { await until(() => uploads === 1); });
      if (offline) {
        await act(async () => { root.root.findByType(ErrorNotice).props.onRetry(); await until(() => !!rejectRetry); });
        assert.equal(root.root.findByType(ErrorNotice).props.retrying, true);
        await act(async () => { rejectRetry!(); });
        assert.equal(root.root.findByType(ErrorNotice).props.retrying, false);
        await act(async () => { root.root.findByType(ErrorNotice).props.onRetry(); await until(() => uploads === 3); });
      }
      assert.equal(root.root.findByType(ClarifyCard).props.value, "30000");
      assert.equal(root.root.findAllByType(ErrorNotice).length, 0);
      assert.equal(router.state.location.pathname, "/record");
      assert.equal(microphones, 1); assert.equal(stopped, 1);
      assert.equal(new Set(keys).size, 1);
      assert.equal(root.root.findAllByType("audio").length, 0);
      assert.equal(root.root.findAllByType("button").some(b => b.children.includes("음성 답 보내기")), false);
    } finally { act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous; }
  });
}


test("repair submission retries the failed request in place with progress and the same key", async () => {
  localStorage.clear(); sessionStorage.clear(); saveGuidanceSoundEnabled(false);
  useAuthStore.getState().setCookieSession("repair-retry-owner");
  useCandidateStore.getState().setCandidate(null);
  saveRepairDraft({ owner: "repair-retry-owner", draft: { ...createCandidateDraft(snapshot), candidateId: null },
    utterance: snapshot.utterance_text, transcript: snapshot.utterance_text, progress: {},
    failure: { kind: "input", message: "확인해 주세요.", errorCode: "MISSING_REQUIRED_FIELD", problemFields: [] } });
  const previous = api.defaults.adapter;
  let submissions = 0;
  let rejectRetry: (() => void) | undefined;
  const keys: string[] = [];
  api.defaults.adapter = async config => {
    if (config.url?.endsWith("/parse")) {
      submissions++; keys.push(String(config.headers["X-Idempotency-Key"]));
      if (submissions === 1) throw new AxiosError("offline");
      if (submissions === 2) return new Promise((_resolve, reject) => { rejectRetry = () => reject(new AxiosError("still offline")); });
      return { config, status: 200, statusText: "OK", headers: {}, data: { ...snapshot, parser_type: "manual" } };
    }
    return { config, status: 200, statusText: "OK", headers: {}, data: config.url?.endsWith("/unpaid") ? { total_unpaid: 0, counterparties: [] } : { entries: [], daily_credit: [] } };
  };
  const router = createMemoryRouter([{ path: "/record", element: <RecordPage /> }, { path: "/record/confirm", element: <p>confirm</p> }], { initialEntries: ["/record"] });
  let root!: ReactTestRenderer;
  try {
    await act(async () => { root = create(<RouterProvider router={router} />); });
    act(() => root.root.findAllByType("button").find(b => b.children.includes("이어서 입력하기"))!.props.onClick());
    await act(async () => { await root.root.findByType(RepairDialog).props.onSubmit(); });
    assert.equal(root.root.findByType(ErrorNotice).props.kind, "network");
    await act(async () => { root.root.findByType(ErrorNotice).props.onRetry(); await until(() => !!rejectRetry); });
    assert.equal(root.root.findByType(ErrorNotice).props.retrying, true);
    assert.equal(root.root.findByType(RepairDialog).props.draft.total_amount, "10000");
    await act(async () => { rejectRetry!(); });
    assert.equal(root.root.findByType(ErrorNotice).props.retrying, false);
    await act(async () => { root.root.findByType(ErrorNotice).props.onRetry(); await until(() => router.state.location.pathname === "/record/confirm"); });
    assert.equal(new Set(keys).size, 1);
    assert.equal(submissions, 3);
  } finally { act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous; }
});
