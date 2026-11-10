import { useEffect, useRef, useState } from "react";
import { useBlocker, useNavigate } from "react-router-dom";

/** Guard router back and the first webview history entry; closing X still relies on persistence. */
export function useDraftLeaveGuard(dirty: boolean, onNativeLeave?: () => void) {
  const navigate = useNavigate();
  const returnHome = useRef(onNativeLeave);
  returnHome.current = onNativeLeave;
  const allowed = useRef(false);
  const [nativeBack, setNativeBack] = useState(false);
  const dirtyRef = useRef(dirty);
  dirtyRef.current = dirty;
  const nativeLeave = useRef<(() => void) | null>(null);
  // The first webview entry has no router predecessor for useBlocker to intercept.
  useEffect(() => {
    const history = window.history;
    if (!dirty || !history || history.state?.idx !== 0 || nativeLeave.current) return;
    const original = { ...history.state };
    delete original.echobillDraftBack;
    const trap = { ...original, echobillDraftBack: true };
    if (!history.state?.echobillDraftBack) history.pushState(trap, "");
    const onPop = (event: PopStateEvent) => {
      if (event.state?.echobillDraftBack || event.state?.idx !== original.idx || event.state?.key !== original.key) return;
      event.stopImmediatePropagation();
      if (!dirtyRef.current || allowed.current) {
        window.removeEventListener("popstate", onPop, true);
        history.back();
        return;
      }
      history.pushState(trap, "");
      setNativeBack(true);
    };
    nativeLeave.current = () => {
      if (returnHome.current) returnHome.current();
      else {
        allowed.current = true;
        navigate("/record", { replace: true });
      }
    };
    window.addEventListener("popstate", onPop, true);
    return () => {
      window.removeEventListener("popstate", onPop, true);
      nativeLeave.current = null;
    };
  }, [dirty, navigate]);
  // A completed draft must not grant permission to leave later, newly edited work.
  useEffect(() => { if (!dirty) { allowed.current = false; setNativeBack(false); } }, [dirty]);
  const blocker = useBlocker(({ currentLocation, nextLocation }) =>
    dirty && !allowed.current && nextLocation.pathname !== "/login" &&
    nextLocation.pathname !== "/auth/kakao/callback" && currentLocation.pathname !== nextLocation.pathname);
  return {
    open: nativeBack || blocker.state === "blocked",
    stay: () => { setNativeBack(false); if (blocker.state === "blocked") blocker.reset(); },
    leave: () => { if (nativeBack) { setNativeBack(false); nativeLeave.current?.(); return; } if (blocker.state === "blocked") blocker.proceed(); },
    allowNavigation: () => { allowed.current = true; },
  };
}
