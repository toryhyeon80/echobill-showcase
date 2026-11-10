import { test } from "node:test";
import assert from "node:assert/strict";
import { renderToStaticMarkup } from "react-dom/server";
import { StaticRouter } from "react-router-dom/server";
import DesignPreview from "../src/pages/DesignPreview";
import "./review-fixture";

/** Match the visible screen's state before comparing component layout. */
function preview(query: string) {
  const previous = Object.getOwnPropertyDescriptor(globalThis, "location");
  Object.defineProperty(globalThis, "location", { configurable: true, value: new URL(`https://fixture.invalid/design?compare=1&${query}`) });
  try { return renderToStaticMarkup(<StaticRouter location="/design"><DesignPreview /></StaticRouter>); }
  finally { if (previous) Object.defineProperty(globalThis, "location", previous); else Reflect.deleteProperty(globalThis, "location"); }
}

test("tax preview includes the same month control and permits an unregistered single-row comparison", () => {
  const html = preview("screen=tax&business=empty&rows=one");
  assert.match(html, /거래 월/); assert.match(html, /type="month"/); assert.match(html, /value="2026-10"/);
  assert.match(html, /사업자 정보를 먼저 등록해 주세요/);
  assert.doesNotMatch(html, /새벽상회|해담정육/);
});

test("400 correction preview uses the real repair sheet instead of an inline candidate question", () => {
  const html = preview("screen=clarify");
  assert.match(html, /role="dialog"[^>]*aria-modal="true"[^>]*aria-label="빠진 내용을 알려 주세요"/);
  assert.match(html, /외상이에요, 현금이에요/);
  assert.match(html, /원문으로 돌아가기/);
  assert.doesNotMatch(html, /이 내용이 맞나요/);
});
