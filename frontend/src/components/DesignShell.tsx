import { Navigate, Outlet, useLocation } from "react-router-dom";
import { useCallback, useEffect, useRef, useState, useSyncExternalStore } from "react";
import { storageRestricted, subscribeStorage } from "../utils/browserStorage";
import { restoreAppSession, useAuthStore, useToastStore } from "../stores/appStore";
import { RequestError } from "./RequestError";
import { AppTabs } from "./AppTabs";
import { RotateNotice } from "../ui/RotateNotice";
import { Toast } from "../ui/Toast";
import { DotsSpinner } from "../ui/DotsSpinner";
import { startStartupLedger } from "../utils/startupLedger";
import { kstToday } from "../utils/useLedgerData";

/** Retain session restoration and keyboard recovery around the published mobile screens. */
export function DesignShell() {
  const [restored, setRestored] = useState(false);
  const storageWarning = useSyncExternalStore(subscribeStorage, storageRestricted, () => false);
  const location = useLocation();
  const shellRef = useRef<HTMLDivElement>(null);
  const userId = useAuthStore((state) => state.userId);
  const sessionError = useAuthStore((state) => state.sessionError);
  const startupMonth = useRef(["/", "/record"].includes(location.pathname) ? kstToday().slice(0, 7) : null);
  // Initial entry and the outage retry share one path: auth → adopt startup reads → saved candidate (#141 · #142)
  const restoreSession = useCallback(async () => {
    const preload = startupMonth.current ? startStartupLedger(startupMonth.current) : null;
    const signedIn = await restoreAppSession((owner) => preload?.adopt(owner));
    if (!signedIn) preload?.discard();
  }, []);
  const message = useToastStore((state) => state.message);
  const clearToast = useToastStore((state) => state.clear);
  const standalone = ["/record/confirm", "/my-page", "/login", "/auth/kakao/callback"].includes(location.pathname);
  useEffect(() => { window.scrollTo({ top: 0 }); }, [location.pathname]);
  useEffect(() => {
    let active = true;
    void restoreSession().then(() => {
      if (!active) return;
      setRestored(true);
    });
    return () => { active = false; };
  }, [restoreSession]);
  useEffect(() => {
    const expire = () => useAuthStore.getState().setCookieSession(null);
    window.addEventListener("echobill:session-expired", expire);
    return () => window.removeEventListener("echobill:session-expired", expire);
  }, []);
  useEffect(() => {
    const viewport = window.visualViewport;
    if (!viewport) return;
    const reveal = () => {
      const field = document.activeElement;
      if (!(field instanceof HTMLInputElement || field instanceof HTMLTextAreaElement || field instanceof HTMLSelectElement) || !shellRef.current?.contains(field)) return;
      const bounds = field.getBoundingClientRect();
      if (bounds.bottom > viewport.offsetTop + viewport.height - 16 || bounds.top < viewport.offsetTop + 16) field.scrollIntoView({ block: "center" });
    };
    viewport.addEventListener("resize", reveal);
    return () => viewport.removeEventListener("resize", reveal);
  }, []);
  if (!restored) return <div className="eb app-frame"><p role="status" className="flex min-h-[100dvh] items-center justify-center gap-2"><DotsSpinner /> 로그인을 확인하고 있어요…</p></div>;
  if (sessionError) return <div className="eb app-frame"><RequestError failure={sessionError} retry={() => void restoreSession()} /></div>;
  if (!userId) return <Navigate to="/login" replace />;
  return <div className="eb app-frame" ref={shellRef}>
    <RotateNotice />
    <div className="app-content">
      {storageWarning && <p className="notice" role="status">이 브라우저에서 임시 보관이 제한되어 있어요. 화면을 닫으면 작성 내용이나 처리 확인 정보가 사라질 수 있어요. 요청 결과를 확인한 뒤 이동해 주세요.</p>}
      <Outlet key={userId ?? "anonymous"} />
    </div>
    {!standalone && <AppTabs />}
    <Toast message={message ? { text: message } : null} onDone={clearToast} offset={location.pathname === "/record/confirm" || location.pathname === "/record" ? 180 : 96} />
  </div>;
}
