import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import DesignPreview from "../src/pages/DesignPreview";
import { RecordHomeView } from "../src/ui/RecordHomeView";
import { MyPageView } from "../src/ui/MyPageView";
import { MoveCreditView } from "../src/ui/MoveCreditView";
import { api } from "../src/api/client";
import "./review-fixture";

for (const screen of ["home", "mypage"] as const) {
  test(`${screen} migration proposal opens and returns without creating a ledger entry`, async () => {
    const previousLocation = Object.getOwnPropertyDescriptor(globalThis, "location");
    const adapter = api.defaults.adapter;
    let calls = 0, root: ReactTestRenderer | undefined;
    api.defaults.adapter = async () => { calls++; throw new Error("preview must not contact any API"); };
    Object.defineProperty(globalThis, "location", { configurable: true, value: new URL(`https://fixture.invalid/design?screen=${screen}&homeState=first`) });
    const router = createMemoryRouter([{ path: "/design", element: <DesignPreview /> }], { initialEntries: ["/design"] });
    try {
      await act(async () => { root = create(<RouterProvider router={router} />); });
      const entry = () => screen === "home" ? root!.root.findByType(RecordHomeView) : root!.root.findByType(MyPageView);
      assert.equal(root.root.findAllByType(MoveCreditView).length, 0);
      act(() => entry().props.onMoveExisting());
      assert.equal(root.root.findAllByType(MoveCreditView).length, 1);
      act(() => root!.root.findByType(MoveCreditView).props.onBack());
      assert.equal(root.root.findAllByType(MoveCreditView).length, 0);
      act(() => entry().props.onMoveExisting());
      act(() => root!.root.findByType(MoveCreditView).props.onSubmit({ counterparty: "검토상회", amount: 30000, when: "last_month" }));
      assert.equal(root.root.findAllByType(MoveCreditView).length, 0);
      assert.match(JSON.stringify(root.toJSON()), /실제로 저장하지 않았어요/);
      assert.equal(calls, 0);
    } finally {
      await act(async () => root?.unmount()); router.dispose(); api.defaults.adapter = adapter;
      if (previousLocation) Object.defineProperty(globalThis, "location", previousLocation); else Reflect.deleteProperty(globalThis, "location");
    }
  });
}

test("the first-use proposal has an explicit skip action without affecting normal recording", async () => {
  let skipped = 0, recorded = 0, root!: ReactTestRenderer;
  const props = { firstUse: true, unpaidTotal: null, recent: [], onMoveExisting: () => {},
    onSkipExisting: () => skipped++, onRecord: () => recorded++, onSubmitText: () => {}, onOpenLedger: () => {}, onOpenEntry: () => {} };
  try {
    act(() => { root = create(<RecordHomeView {...props} />); });
    const button = root.root.findAllByType("button").find((node) => node.children.includes("없어요"))!;
    act(() => button.props.onClick());
    assert.equal(skipped, 1); assert.equal(recorded, 0);
    act(() => root.update(<RecordHomeView {...props} onSkipExisting={undefined} />));
    assert.doesNotMatch(JSON.stringify(root.toJSON()), /예전에 적어 둔 외상이 있으세요/);
    assert.match(JSON.stringify(root.toJSON()), /이미 있는 외상 옮기기/);
  } finally { act(() => root?.unmount()); }
});
