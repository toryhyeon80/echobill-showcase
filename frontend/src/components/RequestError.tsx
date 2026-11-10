import { useEffect, useRef, useState } from "react";
import { Link, useNavigate } from "react-router-dom";
import { errorNoticeKind, type ApiFailure } from "../utils/apiError";
import { ErrorNotice } from "../ui/ErrorNotice";

/** Offer the recovery action appropriate to the failure. */
export function RequestError({ failure, retry, onDismiss }: { failure: ApiFailure; retry: () => void | Promise<void>; onDismiss?: () => void }) {
  const [retrying, setRetrying] = useState(false);
  const inFlight = useRef(false);
  const mounted = useRef(true);
  useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
  async function retryRequest() {
    if (inFlight.current) return;
    inFlight.current = true; setRetrying(true);
    try { await retry(); } finally { inFlight.current = false; if (mounted.current) setRetrying(false); }
  }
  const navigate = useNavigate();
  const [dismissed, setDismissed] = useState<ApiFailure | null>(null);
  const attempt = useRef({ failure, revision: 0 });
  if (attempt.current.failure !== failure) attempt.current = { failure, revision: attempt.current.revision + 1 };
  const kind = errorNoticeKind(failure);
  if (kind && dismissed !== failure) return <ErrorNotice key={`${kind}:${attempt.current.revision}`} kind={kind} retryAfter={failure.retryAfter} retrying={retrying}
    onClose={() => { setDismissed(failure); onDismiss?.(); }} onType={() => navigate("/record")}
    onRetry={() => {
      if (kind === "notFound" || kind === "expired") navigate("/record");
      else if (kind === "conflict" || kind === "notConfirmed" || kind === "alreadyConfirmed") navigate("/ledger");
      else void retryRequest();
    }} />;
  return <div role="alert" className="banner banner--error">
    <p>{failure.message}</p>
    {failure.kind === "auth" ? <Link className="text-link" to="/login">로그인하기</Link>
      : <button className="text-link" onClick={() => { if (kind) setDismissed(null); else void retryRequest(); }}>{kind ? "안내 다시 보기" : "다시 불러오기"}</button>}
  </div>;
}
