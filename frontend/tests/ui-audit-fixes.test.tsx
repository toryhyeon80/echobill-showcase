import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { AmountText } from "../src/ui/AmountText";
import { SettlementSheet } from "../src/ui/SettlementSheet";
import { noop } from "./review-fixture";
import type { SettlementView } from "../src/ui/types";

test("amount fits its measured container, grows back after resize, and releases observation", async () => {
  const original = globalThis.ResizeObserver;
  let resized!: () => void;
  let disconnected = false;
  const box = { clientWidth: 240 };
  const text = { style: { fontSize: "40px" }, getBoundingClientRect: () => ({ width: 480 }) };
  Object.assign(globalThis, { ResizeObserver: class {
    constructor(callback: () => void) { resized = callback; }
    observe() {}
    disconnect() { disconnected = true; }
  } });
  let root!: ReactTestRenderer;
  try {
    await act(async () => { root = create(<AmountText value={123456789000} />, {
      createNodeMock: (element) => element.props.style?.whiteSpace === "nowrap" ? text : box,
    }); });
    assert.equal(text.style.fontSize, "20px");
    assert.ok(JSON.stringify(root.toJSON()).includes("123,456,789,000원"), "full value remains readable");
    box.clientWidth = 480;
    resized();
    assert.equal(text.style.fontSize, "40px");
    await act(async () => root.unmount());
    assert.ok(disconnected);
    text.style.fontSize = "27px";
    resized();
    assert.equal(text.style.fontSize, "27px", "late resize cannot change an unmounted amount");
  } finally {
    Object.assign(globalThis, { ResizeObserver: original });
  }
});

test("blocked clipboard gives manual-copy guidance, selects the text, and does not claim success", async () => {
  const descriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
  let blocked = true;
  let selected = 0;
  let focused = 0;
  let copied = 0;
  Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: {
    writeText: async () => { if (blocked) throw new Error("clipboard refused"); },
  } } });
  const detail: SettlementView = { counterparty: "테스트상회", creditTotal: 45000, paidTotal: 0, unpaid: 45000,
    entries: [{ id: "one", dateLabel: "10월 20일", total: 45000, allocated: 0, remaining: 45000 }], payments: [] };
  let root!: ReactTestRenderer;
  const button = (label: string) => root.root.findAllByType("button").find((node) => node.children.filter((child) => typeof child === "string").join("").includes(label))!;
  try {
    await act(async () => { root = create(<SettlementSheet detail={detail} onClose={noop} onPayment={noop} onCopied={() => copied++} />, {
      createNodeMock: (element) => element.type === "textarea" ? { focus: () => focused++, select: () => selected++, style: {}, scrollHeight: 300 } : null,
    }); });
    await act(async () => button("외상 내역 보내기").props.onClick());
    await act(async () => { await button("글 복사하기").props.onClick(); });
    assert.equal(copied, 0);
    assert.equal(selected, 1);
    assert.equal(focused, 1);
    assert.match(JSON.stringify(root.toJSON()), /자동 복사가 안 돼요/);
    await act(async () => button("정산 내역").props.onClick());
    await act(async () => button("외상 내역 보내기").props.onClick());
    assert.doesNotMatch(JSON.stringify(root.toJSON()), /자동 복사가 안 돼요/, "reopening preview does not retain a previous failure");
    blocked = false;
    await act(async () => { await button("글 복사하기").props.onClick(); });
    assert.equal(copied, 1);
    assert.doesNotMatch(JSON.stringify(root.toJSON()), /자동 복사가 안 돼요/);
  } finally {
    if (root) await act(async () => root.unmount());
    if (descriptor) Object.defineProperty(globalThis, "navigator", descriptor);
    else Reflect.deleteProperty(globalThis, "navigator");
  }
});
