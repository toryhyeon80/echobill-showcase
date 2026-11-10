import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { SettlementSheet } from "../src/ui/SettlementSheet";
import type { SettlementView } from "../src/ui/types";
import "./review-fixture";

const detail: SettlementView = { counterparty: "가상상회", creditTotal: 10000, paidTotal: 0, unpaid: 10000,
  entries: [{ id: "one", dateLabel: "10월 23일", total: 10000, allocated: 0, remaining: 10000 }], payments: [] };

for (const transition of ["close", "replace", "back", "unmount"] as const) {
  for (const failed of [false, true]) {
    test(`late clipboard ${failed ? "failure" : "success"} is ignored after ${transition}`, async () => {
      const descriptor = Object.getOwnPropertyDescriptor(globalThis, "navigator");
      let release: (() => void) | undefined, copied = 0, selected = 0, root: ReactTestRenderer | undefined;
      Object.defineProperty(globalThis, "navigator", { configurable: true, value: { clipboard: {
        writeText: () => new Promise<void>((resolve, reject) => { release = () => failed ? reject(new Error("denied")) : resolve(); }),
      } } });
      const view = (source: SettlementView | null) => <SettlementSheet detail={source} onClose={() => {}} onPayment={() => {}} onCopied={() => copied++} />;
      const click = (text: string) => root!.root.findAllByType("button").find((node) =>
        node.children.some((child) => typeof child === "string" && child.trim() === text))!.props.onClick();
      try {
        act(() => { root = create(view(detail), { createNodeMock: (element) => element.type === "textarea"
          ? { focus() {}, select: () => selected++, style: {}, scrollHeight: 100 } : null }); });
        act(() => click("외상 내역 보내기"));
        let copying!: Promise<void>;
        act(() => { copying = click("글 복사하기"); });
        act(() => {
          if (transition === "unmount") { root!.unmount(); root = undefined; }
          else if (transition === "back") click("정산 내역");
          else root!.update(view(transition === "close" ? null : { ...detail, counterparty: "다른상회" }));
        });
        await act(async () => { release!(); await copying; });
        assert.equal(copied, 0, "an old copy must not announce success on the current screen");
        assert.equal(selected, 0, "an old failure must not move focus into the current preview");
        if (root) {
          if (transition === "close") act(() => root!.update(view(detail)));
          act(() => click("외상 내역 보내기"));
          assert.doesNotMatch(JSON.stringify(root.toJSON()), /자동 복사가 안 돼요/);
        }
      } finally {
        act(() => root?.unmount()); release?.();
        if (descriptor) Object.defineProperty(globalThis, "navigator", descriptor); else Reflect.deleteProperty(globalThis, "navigator");
      }
    });
  }
}
