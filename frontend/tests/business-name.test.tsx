import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { api } from "../src/api/client";
import { useAuthStore } from "../src/stores/appStore";
import { useBusinessName } from "../src/utils/useBusinessName";
import "./review-fixture";

/** Exercise the optional signature through close/reopen and session replacement. */
function Name({ active }: { active: boolean }) {
  return <p data-name={useBusinessName(active)} />;
}

test("reopening detail never shows a cached signature while the new business name is pending", async () => {
  const previous = api.defaults.adapter;
  let root!: ReactTestRenderer;
  let reads = 0;
  let resolveName!: () => void;
  useAuthStore.getState().setCookieSession("owner");
  api.defaults.adapter = async (config) => {
    reads++;
    const response = (name: string) => ({ config, status: 200, statusText: "OK", headers: {}, data: { registered: true, business_name: name } });
    if (reads === 1) return response("바꾸기 전 가게");
    return new Promise((resolve) => { resolveName = () => resolve(response("바꾼 가게")); });
  };
  try {
    await act(async () => { root = create(<Name active />); });
    assert.equal(root.root.findByType("p").props["data-name"], "바꾸기 전 가게");
    act(() => root.update(<Name active={false} />));
    await act(async () => root.update(<Name active />));
    assert.equal(root.root.findByType("p").props["data-name"], null, "never offer an outdated signature for copying");
    await act(async () => resolveName());
    assert.equal(root.root.findByType("p").props["data-name"], "바꾼 가게");
  } finally { act(() => root?.unmount()); api.defaults.adapter = previous; }
});

for (const owner of ["owner", "other-owner"]) {
  test(`late business name from a replaced session is ignored (${owner})`, async () => {
    const previous = api.defaults.adapter;
    let root!: ReactTestRenderer;
    let resolveOld!: () => void;
    let reads = 0;
    useAuthStore.getState().setCookieSession("owner");
    api.defaults.adapter = async (config) => {
      const response = (name: string) => ({ config, status: 200, statusText: "OK", headers: {}, data: { registered: true, business_name: name } });
      if (++reads === 1) return new Promise((resolve) => { resolveOld = () => resolve(response("이전 세션 가게")); });
      return response("새 세션 가게");
    };
    try {
      await act(async () => { root = create(<Name active />); });
      await act(async () => useAuthStore.getState().setCookieSession(owner));
      assert.equal(root.root.findByType("p").props["data-name"], "새 세션 가게");
      await act(async () => resolveOld());
      assert.equal(root.root.findByType("p").props["data-name"], "새 세션 가게");
    } finally { act(() => root?.unmount()); api.defaults.adapter = previous; }
  });
}

test("business lookup failure leaves the optional signature empty", async () => {
  const previous = api.defaults.adapter;
  let root!: ReactTestRenderer;
  useAuthStore.getState().setCookieSession("owner");
  api.defaults.adapter = async () => { throw new Error("synthetic offline"); };
  try {
    await act(async () => { root = create(<Name active />); });
    assert.equal(root.root.findByType("p").props["data-name"], null);
  } finally { act(() => root?.unmount()); api.defaults.adapter = previous; }
});
