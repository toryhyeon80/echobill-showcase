import { test } from "node:test";
import assert from "node:assert/strict";
import { AxiosError } from "axios";
import { api } from "../src/api/client";
import { useAuthStore, useCandidateStore } from "../src/stores/appStore";
import { snapshot, until } from "./review-fixture";

for (const transition of ["logout", "replacement"] as const) {
  for (const result of ["candidate", "missing", "network"] as const) {
    test(`candidate restoration ignores an old ${result} response after ${transition}`, async () => {
      const previous = api.defaults.adapter;
      localStorage.clear(); sessionStorage.clear();
      useAuthStore.getState().setCookieSession("restore-owner");
      useCandidateStore.getState().setCandidate(snapshot);
      useCandidateStore.setState({ candidate: null, hydrated: false, restoreError: null });
      let release: (() => void) | undefined;
      api.defaults.adapter = (config) => new Promise((resolve, reject) => { release = () => {
        if (result === "network") reject(new Error("old offline read"));
        else if (result === "missing") reject(new AxiosError("missing", undefined, config, undefined,
          { config, status: 404, statusText: "Not Found", headers: {}, data: {} }));
        else resolve({ config, status: 200, statusText: "OK", headers: {}, data: { ...snapshot, status: "candidate" } });
      }; });
      try {
        const pending = useCandidateStore.getState().hydrateFromStorage();
        await until(() => !!release);
        useAuthStore.getState().setCookieSession(transition === "logout" ? null : "restore-owner");
        release!(); await pending;
        assert.equal(useCandidateStore.getState().candidate, null);
        assert.equal(useCandidateStore.getState().restoreError, null);
        assert.ok(localStorage.getItem("echobill_last_candidate"), "an old failure cannot erase the restore pointer");
      } finally {
        release?.(); api.defaults.adapter = previous; useCandidateStore.getState().setCandidate(null);
        localStorage.clear(); sessionStorage.clear();
      }
    });
  }
}
