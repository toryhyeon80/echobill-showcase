import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { api } from "../src/api/client";
import { MyPage } from "../src/pages/MyPage";
import { MyPageView } from "../src/ui/MyPageView";
import { DocumentSheet } from "../src/ui/DocumentSheet";
import { useAuthStore } from "../src/stores/appStore";
import { TERMS_META } from "../src/ui/termsOfService";
import { guidanceSoundEnabled, saveGuidanceSoundEnabled } from "../src/utils/guidanceAudio";
import "./review-fixture";

test("account menus open the current terms and privacy documents without accepting or changing consent", async () => {
  localStorage.clear(); sessionStorage.clear();
  useAuthStore.getState().setCookieSession("document-owner", [], false);
  const previous = api.defaults.adapter;
  let writes = 0, root!: ReactTestRenderer;
  api.defaults.adapter = async (config) => {
    if (config.method !== "get") writes++;
    return { config, status: 200, statusText: "OK", headers: {}, data: config.url?.endsWith("/auth/me")
      ? { user_id: "document-owner", ai_consent: false } : { registered: false } };
  };
  const router = createMemoryRouter([{ path: "/my-page", element: <MyPage /> }], { initialEntries: ["/my-page"] });
  try {
    await act(async () => { root = create(<RouterProvider router={router} />); });
    assert.equal(typeof root.root.findByType(MyPageView).props.onMoveExisting, "function", "외상 옮기기 menu is live (PM 10/23)");
    assert.equal(root.root.findByType(MyPageView).props.guidanceSound, true);
    act(() => root.root.findByType(MyPageView).props.onGuidanceSoundChange(false));
    assert.equal(guidanceSoundEnabled(), false);
    assert.equal(root.root.findByType(MyPageView).props.guidanceSound, false);
    act(() => saveGuidanceSoundEnabled(true));
    assert.equal(root.root.findByType(MyPageView).props.guidanceSound, true, "question-screen setting stays synchronized");
    const menus = root.root.findAllByType("button");
    const terms = menus.find((button) => button.findAllByType("span").some((span) => span.children.includes("이용약관")));
    assert.ok(terms, "terms must be reachable after signup from the account menu");
    act(() => terms.props.onClick());
    assert.equal(root.root.findByType(DocumentSheet).props.doc, "terms");
    assert.match(JSON.stringify(root.toJSON()), new RegExp(TERMS_META.version));
    act(() => root.root.findByType(DocumentSheet).props.onClose());
    assert.equal(root.root.findByType(DocumentSheet).props.doc, null);
    const privacy = root.root.findAllByType("button").find((button) => button.findAllByType("span").some((span) => span.children.includes("개인정보 처리방침")))!;
    act(() => privacy.props.onClick());
    assert.equal(root.root.findByType(DocumentSheet).props.doc, "privacy");
    assert.equal(useAuthStore.getState().aiConsent, false); assert.equal(writes, 0);
  } finally { act(() => root?.unmount()); router.dispose(); api.defaults.adapter = previous; localStorage.clear(); sessionStorage.clear(); }
});
