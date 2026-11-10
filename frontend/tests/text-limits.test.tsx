import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createCandidateDraft, draftProblems } from "../src/utils/candidateDraft";
import { applyVoiceAnswer, pendingClarifications } from "../src/utils/clarification";
import { BusinessInfoSheet } from "../src/ui/BusinessInfoSheet";
import { BusinessInfoDialog } from "../src/components/BusinessInfoDialog";
import { UnderlineField } from "../src/ui/UnderlineField";
import { RepairDialog } from "../src/components/RepairDialog";
import { ClarificationCard } from "../src/components/ClarificationCard";
import { ClarifyCard } from "../src/ui/ClarifyCard";
import { snapshot } from "./review-fixture";

test("a counterparty question explains its text limit instead of asking to fix numbers", () => {
  const draft = createCandidateDraft(snapshot);
  draft.counterparty = "가".repeat(201);
  let root!: ReactTestRenderer;
  try {
    act(() => { root = create(<ClarificationCard draft={draft} busy={false} onChange={() => {}} onFinish={() => {}} onClose={() => {}} />); });
    assert.match(root.root.findByType(ClarifyCard).props.inputError ?? "", /거래처.*200/);
    assert.doesNotMatch(JSON.stringify(root.toJSON()), /숫자와 허용 범위/);
  } finally { act(() => root?.unmount()); }
});

for (const field of ["counterparty", "name", "unit"] as const) {
  test(`candidate ${field} is rejected locally at the server text limit`, () => {
    const draft = createCandidateDraft(snapshot);
    if (field === "counterparty") draft.counterparty = "가".repeat(201);
    else draft.items[0][field] = "가".repeat(field === "unit" ? 51 : 201);
    const path = field === "counterparty" ? field : `items[0].${field}`;
    assert.ok(draftProblems(draft).some((problem) => problem.field === path));
    if (field === "counterparty") assert.ok(pendingClarifications(draft).includes(field));
  });
}

test("text limits count Unicode characters after trimming, as the server does", () => {
  const draft = createCandidateDraft(snapshot);
  draft.counterparty = ` ${"가".repeat(200)} `;
  draft.items[0].name = "😀".repeat(200);
  draft.items[0].unit = "😀".repeat(50);
  assert.deepEqual(draftProblems(draft), []);
  assert.ok(applyVoiceAnswer(draft, "counterparty", { field: "counterparty", status: "ok", value: "😀".repeat(200) }));
  assert.ok(applyVoiceAnswer(draft, "items[0].qty", { field: "qty", status: "ok", value: 2, unit: "😀".repeat(50) }));
});

test("business names over 200 characters show a field error and cannot be submitted", () => {
  let root!: ReactTestRenderer, writes = 0;
  try {
    act(() => { root = create(<BusinessInfoDialog busy={false} error={null} onClose={() => {}} onSave={async () => { writes++; }} />); });
    const fields = () => root.root.findAllByType(UnderlineField);
    act(() => { fields()[0].props.onChange("가".repeat(201)); fields()[1].props.onChange("1234567890"); });
    assert.match(fields()[0].props.error ?? "", /200/);
    const save = root.root.findAllByType("button").find((node) => node.children.includes("저장하기"))!;
    assert.equal(save.props.disabled, true);
    act(() => root.root.findByType(BusinessInfoSheet).props.onSubmit({ name: "가".repeat(201), numberDigits: "1234567890" }));
    assert.equal(writes, 0, "the submit handler must enforce the limit as well as the button");
    act(() => fields()[0].props.onChange("가".repeat(200)));
    assert.equal(root.root.findAllByType("button").find((node) => node.children.includes("저장하기"))!.props.disabled, false);
  } finally { act(() => root?.unmount()); }
});

test("repair form identifies the actual invalid field instead of showing only a generic summary", () => {
  const draft = createCandidateDraft(snapshot);
  draft.items[0].name = "가".repeat(201);
  let root!: ReactTestRenderer;
  try {
    act(() => { root = create(<RepairDialog draft={draft} message="확인해 주세요" problemFields={[]} busy={false} submitError={null}
      onChange={() => {}} onSubmit={() => {}} onClose={() => {}} />); });
    const name = root.root.findAllByType("input").find((node) => node.props["aria-label"] === "품목명")!;
    assert.equal(name.props["aria-invalid"], true);
    assert.ok(name.props["aria-describedby"]);
  } finally { act(() => root?.unmount()); }
});
