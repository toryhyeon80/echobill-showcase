import { lazy, Suspense } from "react";
import { Navigate, Route, Routes } from "react-router-dom";
import { AppShell } from "./components/AppShell";
import { ConfirmPage } from "./pages/ConfirmPage";
import { LedgerPage } from "./pages/LedgerPage";
import { RecordPage } from "./pages/RecordPage";
import { RecordingCheckPage } from "./pages/RecordingCheckPage";
import { MyPage } from "./pages/MyPage";
import { TaxInvoicePage } from "./pages/TaxInvoicePage";
import { LoginPage } from "./pages/LoginPage";

/** Dev-only design preview (DESIGN.md); the import is dropped from production builds. */
const DesignPreview = import.meta.env.DEV ? lazy(() => import("./pages/DesignPreview")) : null;

/**
 * In-app 3-tab routes per SPEC §4.
 * Candidate review is separate from text entry; feedback API wiring follows D09.
 */
export default function App() {
  return (
    <Routes>
      <Route element={<AppShell />}>
        <Route index element={<Navigate to="/record" replace />} />
        <Route path="/record" element={<RecordPage />} />
        <Route path="/record/confirm" element={<ConfirmPage />} />
        <Route path="/recording-check" element={<RecordingCheckPage />} />
        <Route path="/ledger" element={<LedgerPage />} />
        <Route path="/tax-invoice" element={<TaxInvoicePage />} />
        <Route path="/my-page" element={<MyPage />} />
      </Route>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/auth/kakao/callback" element={<LoginPage callback />} />
      {DesignPreview && (
        <Route
          path="/design"
          element={
            <Suspense fallback={null}>
              <DesignPreview />
            </Suspense>
          }
        />
      )}
      <Route path="*" element={<Navigate to="/record" replace />} />
    </Routes>
  );
}
