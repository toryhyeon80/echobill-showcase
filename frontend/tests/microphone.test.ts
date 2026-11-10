import { test } from "node:test";
import assert from "node:assert/strict";
import { requestMicrophone, microphoneMessage } from "../src/utils/microphone";
import { renderToStaticMarkup } from "react-dom/server";
import { createElement } from "react";
import { RecordingOverlay } from "../src/ui/RecordingOverlay";

/** Synthetic streams exercise cleanup without opening a real microphone. */
function deferredMicrophone() {
  let resolve!: (value: MediaStream) => void;
  let reject!: (reason: unknown) => void;
  let stopped = 0;
  const stream = { getTracks: () => [{ stop: () => { stopped++; } }] } as unknown as MediaStream;
  const devices = { getUserMedia: () => new Promise<MediaStream>((yes, no) => { resolve = yes; reject = no; }) };
  return { devices, stream, resolve: () => resolve(stream), reject: (reason: unknown) => reject(reason), stopped: () => stopped };
}

test("permission timeout rejects and disposes a stream approved afterwards", async () => {
  const mic = deferredMicrophone();
  const waiting = requestMicrophone(new AbortController().signal, 5, mic.devices);
  await assert.rejects(waiting, { name: "TimeoutError" });
  mic.resolve(); await Promise.resolve();
  assert.equal(mic.stopped(), 1);
});

test("cancelled permission cannot stop a newer microphone request", async () => {
  const old = deferredMicrophone(); const next = deferredMicrophone();
  const controller = new AbortController();
  const waiting = requestMicrophone(controller.signal, 1000, old.devices);
  controller.abort();
  await assert.rejects(waiting, { name: "AbortError" });
  const current = requestMicrophone(new AbortController().signal, 1000, next.devices);
  next.resolve(); assert.equal(await current, next.stream);
  old.resolve(); await Promise.resolve();
  assert.equal(old.stopped(), 1); assert.equal(next.stopped(), 0);
});

test("permission denial remains distinguishable and cancellation before start never opens a device", async () => {
  const mic = deferredMicrophone();
  const waiting = requestMicrophone(new AbortController().signal, 1000, mic.devices);
  mic.reject(new DOMException("denied", "NotAllowedError"));
  await assert.rejects(waiting, { name: "NotAllowedError" });
  assert.match(microphoneMessage(new DOMException("denied", "NotAllowedError")), /권한/);
  let calls = 0;
  const controller = new AbortController(); controller.abort();
  await assert.rejects(requestMicrophone(controller.signal, 1000, { getUserMedia: async () => { calls++; return mic.stream; } }), { name: "AbortError" });
  assert.equal(calls, 0);
});

test("permission waiting tells the truth and always offers text input and cancellation", () => {
  const noop = () => {};
  const html = renderToStaticMarkup(createElement(RecordingOverlay, { state: "requesting", seconds: 0,
    onStop: noop, onCancel: noop, onRetry: noop, onTypeInstead: noop }));
  assert.match(html, /마이크를 연결하고 있어요/);
  assert.match(html, /취소하고 글자로 입력하기/);
  assert.match(html, /aria-label="닫기"/);
  assert.doesNotMatch(html, /장부로 정리하고 있어요/);
});
