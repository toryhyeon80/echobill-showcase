import { test } from "node:test";
import assert from "node:assert/strict";
import { act, create, type ReactTestRenderer } from "react-test-renderer";
import { createMemoryRouter, RouterProvider } from "react-router-dom";
import { api } from "../src/api/client";
import { MyPage } from "../src/pages/MyPage";
import { TaxInvoicePage } from "../src/pages/TaxInvoicePage";
import { MyPageView } from "../src/ui/MyPageView";
import { TaxInvoiceView } from "../src/ui/TaxInvoiceView";
import { useAuthStore } from "../src/stores/appStore";
import { until } from "./review-fixture";

for (const screen of ["account", "tax"] as const) {
  for (const delayed of [false, true]) {
    test(`${screen}: replacing the same owner's session clears old business data (${delayed ? "pending" : "loaded"})`, async () => {
      const previous = api.defaults.adapter;
      localStorage.clear(); sessionStorage.clear();
      useAuthStore.getState().setCookieSession("same-owner", [], false);
      let round = 1, reads = 0, root!: ReactTestRenderer;
      const release: Array<() => void> = [];
      api.defaults.adapter = (config) => {
        const response = (data: unknown) => ({ config, status: 200, statusText: "OK", headers: {}, data });
        if (config.url?.endsWith("/auth/me")) return Promise.resolve(response({ user_id: "same-owner", ai_consent: false }));
        if (config.url?.endsWith("/business")) {
          reads++;
          const data = { registered: true, business_name: round === 1 ? "이전사업자" : "새사업자", business_number_masked: "***" };
          return round === 1 && !delayed ? Promise.resolve(response(data)) : new Promise((resolve) => release.push(() => resolve(response(data))));
        }
        return Promise.resolve(response(config.url === "/api/v1/tax" ? { invoices: [] } : { entries: [], daily_credit: [] }));
      };
      const router = createMemoryRouter([{ path: "/", element: screen === "account" ? <MyPage /> : <TaxInvoicePage /> }]);
      const businessName = () => screen === "account" ? root.root.findByType(MyPageView).props.business?.name
        : root.root.findAllByType(TaxInvoiceView)[0]?.props.business?.name;
      try {
        await act(async () => { root = create(<RouterProvider router={router} />); await until(() => reads === 1); });
        if (!delayed) assert.equal(businessName(), "이전사업자");
        round = 2;
        await act(async () => useAuthStore.getState().setCookieSession("same-owner", [], false));
        assert.equal(reads, 2, "new sessions must reread the business even for the same user ID");
        assert.notEqual(businessName(), "이전사업자");
        if (delayed) {
          await act(async () => release[0]());
          assert.notEqual(businessName(), "이전사업자", "late old responses cannot become visible");
        }
        await act(async () => release.at(-1)!());
        assert.equal(businessName(), "새사업자");
      } finally {
        act(() => root?.unmount()); router.dispose(); release.forEach((finish) => finish());
        api.defaults.adapter = previous; localStorage.clear(); sessionStorage.clear();
      }
    });
  }
}
