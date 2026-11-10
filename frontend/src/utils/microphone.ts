/** Bound permission waiting; cancelled or late streams must never keep the microphone open. */
export function requestMicrophone(signal: AbortSignal, timeoutMs = 15_000,
  devices: Pick<MediaDevices, "getUserMedia"> = navigator.mediaDevices): Promise<MediaStream> {
  return new Promise((resolve, reject) => {
    let settled = false;
    const finish = (error?: DOMException, stream?: MediaStream) => {
      if (settled) { stream?.getTracks().forEach((track) => track.stop()); return; }
      settled = true;
      clearTimeout(timer);
      signal.removeEventListener("abort", abort);
      if (error) reject(error); else if (stream) resolve(stream);
    };
    const abort = () => finish(new DOMException("Microphone request cancelled", "AbortError"));
    const timer = setTimeout(() => finish(new DOMException("Microphone permission timed out", "TimeoutError")), timeoutMs);
    if (signal.aborted) { abort(); return; }
    signal.addEventListener("abort", abort, { once: true });
    try {
      devices.getUserMedia({ audio: true }).then((stream) => finish(undefined, stream), (error: unknown) => {
        if (settled) return;
        settled = true; clearTimeout(timer); signal.removeEventListener("abort", abort); reject(error);
      });
    } catch (error) {
      settled = true; clearTimeout(timer); signal.removeEventListener("abort", abort); reject(error);
    }
  });
}

/** Explain permission, device and browser failures without exposing customer data. */
export function microphoneMessage(error: unknown): string {
  const name = error instanceof Error ? error.name : "";
  if (name === "TimeoutError") return "마이크 응답이 늦어지고 있어요. 다시 시도하거나 글자로 입력해 주세요. 계속 안 되면 기본 브라우저에서 열어 주세요.";
  if (name === "NotAllowedError" || name === "SecurityError") return "마이크 사용이 허용되지 않았어요. 앱과 브라우저의 마이크 권한을 확인하거나 글자로 입력해 주세요.";
  if (name === "NotFoundError") return "사용할 수 있는 마이크가 없어요. 글자로 입력해 주세요.";
  if (name === "NotReadableError") return "마이크를 열 수 없어요. 통화나 다른 녹음을 종료한 뒤 다시 시도하거나 글자로 입력해 주세요.";
  return "이 브라우저에서 녹음을 시작하지 못했어요. 글자로 입력하거나 기본 브라우저에서 열어 주세요.";
}
