import { test } from "node:test";
import assert from "node:assert/strict";
import { POLICY_META, POLICY_SECTIONS } from "../src/ui/privacyPolicy";
import { TERMS_META } from "../src/ui/termsOfService";

test("v1.3 lists Google, OpenAI's guidance speech and counterparty hints, effective 11/3", () => {
  assert.equal(POLICY_META.version, "v1.3");
  assert.equal(POLICY_META.effectiveDate, "2026년 11월 3일");
  const table = POLICY_SECTIONS.find((section) => section.id === "outsourcing")!.blocks.find((block) => block.kind === "table");
  assert.ok(table && table.kind === "table");
  const google = table.rows.find((row) => row[0].startsWith("Google"));
  assert.ok(google, "Google row");
  assert.match(google[3], /학습에 쓰지 않음/);
  const openai = table.rows.find((row) => row[0].startsWith("OpenAI"));
  assert.ok(openai, "OpenAI row kept");
  assert.match(openai[1], /안내 문장.*음성으로 변환/);  // #220 읽어 주기 (PM 10/26)
  assert.match(openai[2], /최근 거래처 이름/);  // STT 거래처 힌트 (PM 11/3)
});

test("the unchanged terms keep their own effective date", () => {
  assert.deepEqual(TERMS_META, { version: "v1.0", effectiveDate: "2026년 10월 15일" });
});

