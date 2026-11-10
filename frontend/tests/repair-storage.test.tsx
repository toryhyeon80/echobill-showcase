import { test } from "node:test";
import assert from "node:assert/strict";
import { localStore } from "../src/utils/browserStorage";
import { writeDraft } from "../src/utils/draftStorage";
import { createCandidateDraft } from "../src/utils/candidateDraft";
import { readRepairDraft } from "../src/utils/repairStorage";
import { snapshot } from "./review-fixture";

test("corrupt saved clarification fields are discarded without crashing the record page", () => {
  for (const field of [42, {}, [], true]) {
    writeDraft(localStore, "echobill_repair_draft", { owner: "repair-owner", draft: createCandidateDraft(snapshot),
      utterance: "가상 거래", transcript: null, failure: { kind: "input", message: "확인이 필요해요" }, progress: { field } });
    assert.equal(readRepairDraft("repair-owner"), null);
    assert.equal(localStore.getItem("echobill_repair_draft"), null);
  }
});
