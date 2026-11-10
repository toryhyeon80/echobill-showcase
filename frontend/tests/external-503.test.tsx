import { test } from "node:test";
import assert from "node:assert/strict";
import { AxiosError } from "axios";
import { describeApiFailure } from "../src/utils/apiError";

test("503 and legacy 504 both preserve external-service recovery instead of generic server failure", () => {
  for (const status of [503, 504]) {
    const error = new AxiosError("unavailable", undefined, undefined, undefined,
      { status, data: { error_code: "STT_TIMEOUT", message: "음성을 처리하지 못했어요." }, headers: {}, config: { headers: {} } } as never);
    const failure = describeApiFailure(error);
    assert.equal(failure.kind, "external");
    assert.equal(failure.message, "음성을 처리하지 못했어요.");
  }
});
