import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { UnderlineField } from "../src/ui/UnderlineField";
import { BusinessInfoSheet } from "../src/ui/BusinessInfoSheet";
import "./review-fixture";

for (const kind of ["numeric", "decimal"] as const) {
  test(`${kind} input does not turn negative, scientific or malformed input into another valid value`, () => {
    const changes: string[] = [];
    let root!: ReactTestRenderer;
    try {
      act(() => { root = create(<UnderlineField label="가상 숫자" value="" {...{ [kind]: true }} onChange={(value) => changes.push(value)} />); });
      const input = () => root.root.findByType("input");
      for (const value of kind === "numeric" ? ["-100", "1.5", "1e3", "12원"] : ["-1.5", "1e3", "1.2.3", "두2"]) {
        act(() => input().props.onChange({ target: { value } }));
        assert.deepEqual(changes, [], `must reject ${value} rather than silently changing its meaning`);
      }
      for (const value of kind === "numeric" ? ["10,000", "", "0"] : ["1.5", "1.", "", "0.5"]) {
        act(() => input().props.onChange({ target: { value } }));
        assert.equal(changes.at(-1), value.replace(/,/g, ""));
      }
    } finally { act(() => root?.unmount()); }
  });
}

test("formatted amounts retain every entered digit without numeric precision loss", () => {
  let root!: ReactTestRenderer;
  try {
    act(() => { root = create(<UnderlineField label="금액" numeric value="9007199254740993" onChange={() => {}} />); });
    assert.equal(root.root.findByType("input").props.value, "9,007,199,254,740,993");
  } finally { act(() => root?.unmount()); }
});

test("late business information cannot erase user edits; reopening starts a fresh form", () => {
  let root!: ReactTestRenderer;
  const sheet = (open: boolean, initialName: string) => <BusinessInfoSheet open={open} initialName={initialName} onClose={() => {}} onSubmit={() => {}} />;
  const fields = () => root.root.findAllByType(UnderlineField);
  try {
    act(() => { root = create(sheet(true, "")); });
    act(() => { fields()[0].props.onChange("작성중인상호"); fields()[1].props.onChange("1234567890"); });
    act(() => root.update(sheet(true, "늦게도착한상호")));
    assert.equal(fields()[0].props.value, "작성중인상호");
    assert.equal(fields()[1].props.value, "1234567890");
    act(() => root.update(sheet(false, "저장된상호")));
    act(() => root.update(sheet(true, "저장된상호")));
    assert.equal(fields()[0].props.value, "저장된상호");
    assert.equal(fields()[1].props.value, "");
  } finally { act(() => root?.unmount()); }
});
