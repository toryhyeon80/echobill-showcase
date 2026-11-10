import { Button } from "../ui/Button";
import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { microphoneMessage, requestMicrophone } from "../utils/microphone";

interface RecordingInfo {
  mimeType: string;
  extension: string;
  sizeBytes: number;
  playbackUrl: string;
}

/** Temporary in-app diagnostic for Android/iOS microphone and container support. */
export function RecordingCheckPage() {
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const wakeLockRef = useRef<WakeLockSentinel | null>(null);
  const playbackUrlRef = useRef<string | null>(null);
  const [recording, setRecording] = useState(false);
  const [starting, setStarting] = useState(false);
  const pending = useRef<AbortController | null>(null);
  const generation = useRef(0);
  const mounted = useRef(true);
  const [message, setMessage] = useState("녹음 시작을 누르면 마이크 권한을 요청합니다.");
  const [result, setResult] = useState<RecordingInfo | null>(null);

  const stopRecording = useCallback(() => {
    generation.current += 1;
    pending.current?.abort(); pending.current = null;
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    void wakeLockRef.current?.release().catch(() => { if (mounted.current) setMessage("화면 켜짐 유지 해제를 확인하지 못했습니다."); });
    wakeLockRef.current = null;
    if (mounted.current) { setRecording(false); setStarting(false); }
  }, []);

  useEffect(() => {
    mounted.current = true;
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden" && recorderRef.current?.state === "recording") {
        stopRecording();
        setMessage("화면을 벗어나 녹음을 중단했습니다. 결과를 확인한 뒤 다시 시험해 주세요.");
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    window.addEventListener("pagehide", stopRecording);
    return () => {
      mounted.current = false;
      document.removeEventListener("visibilitychange", onVisibilityChange);
      window.removeEventListener("pagehide", stopRecording);
      stopRecording();
      if (playbackUrlRef.current) URL.revokeObjectURL(playbackUrlRef.current);
    };
  }, [stopRecording]);

  async function startRecording() {
    if (pending.current || recorderRef.current?.state === "recording") return;
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setMessage("이 브라우저는 녹음을 지원하지 않습니다. 기기·카카오톡·OS 버전을 기록해 주세요.");
      return;
    }
    const controller = new AbortController();
    pending.current = controller;
    const request = ++generation.current;
    recorderRef.current = null;
    setStarting(true);
    setMessage("마이크 권한을 확인하고 있습니다. 취소할 수도 있어요.");
    try {
      if (playbackUrlRef.current) URL.revokeObjectURL(playbackUrlRef.current);
      playbackUrlRef.current = null;
      setResult(null);
      const stream = await requestMicrophone(controller.signal);
      if (request !== generation.current || !mounted.current) { stream.getTracks().forEach((track) => track.stop()); return; }
      pending.current = null;
      streamRef.current = stream;
      if (document.visibilityState === "hidden") { stopRecording(); setMessage("화면으로 돌아온 뒤 다시 시험해 주세요."); return; }
      const preferred = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"]
        .find((mime) => MediaRecorder.isTypeSupported(mime));
      const recorder = new MediaRecorder(stream, preferred ? { mimeType: preferred } : undefined);
      recorderRef.current = recorder;
      const chunks: BlobPart[] = [];
      recorder.ondataavailable = (event) => { if (mounted.current && recorderRef.current === recorder && event.data.size > 0) chunks.push(event.data); };
      recorder.onstop = () => {
        if (!mounted.current || recorderRef.current !== recorder) return;
        const blob = new Blob(chunks, { type: recorder.mimeType });
        const mimeType = blob.type || recorder.mimeType || "알 수 없음";
        const extension = mimeType.includes("mp4") ? "mp4" : mimeType.includes("webm") ? "webm" : "알 수 없음";
        const playbackUrl = URL.createObjectURL(blob);
        playbackUrlRef.current = playbackUrl;
        setResult({ mimeType, extension, sizeBytes: blob.size, playbackUrl });
        setMessage(blob.size > 0 ? "녹음 파일이 만들어졌습니다. 아래 정보와 재생 결과를 기록해 주세요." : "녹음 파일이 비어 있습니다. 다시 시험해 주세요.");
        recorderRef.current = null;
      };
      recorder.onerror = () => {
        if (!mounted.current || recorderRef.current !== recorder) return;
        stopRecording();
        setMessage("녹음 중 오류가 났습니다. 기기와 브라우저 정보를 기록해 주세요.");
      };
      recorder.start();
      setStarting(false);
      setRecording(true);
      setMessage("녹음 중입니다. 몇 초간 말한 뒤 종료해 주세요.");
      if (navigator.wakeLock) {
        try {
          const lock = await navigator.wakeLock.request("screen");
          if (recorder.state === "recording") wakeLockRef.current = lock;
          else await lock.release();
        } catch { /* Best effort. */ }
      }
    } catch (error) {
      if (request !== generation.current || !mounted.current) return;
      stopRecording();
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
      setMessage(microphoneMessage(error));
    }
  }

  return (
    <section className="page">
      <Link className="text-link back-link" to="/record">← 거래 입력으로</Link>
      <h1 className="page__title">카톡 인앱 녹음 점검</h1>
      <p className="page__hint">Android와 iPhone에서 각각 카카오톡 링크로 열어 시험하세요. 녹음은 이 기기에서만 처리하고 서버에 보내지 않습니다.</p>
      <p className="notice" role="status">{message}</p>
      <Button  className="recording-button" type="button" onClick={recording || starting ? stopRecording : startRecording}>
        {starting ? "마이크 연결 취소" : recording ? "녹음 종료" : "녹음 시작"}
      </Button>
      {result && (
        <div className="notice">
          <h2>녹음 결과</h2>
          <p><strong>MIME 형식:</strong> {result.mimeType}</p>
          <p><strong>확장자:</strong> {result.extension}</p>
          <p><strong>파일 크기:</strong> {result.sizeBytes.toLocaleString("ko-KR")} 바이트</p>
          <audio controls src={result.playbackUrl}>이 브라우저는 오디오 재생을 지원하지 않습니다.</audio>
        </div>
      )}
      <div className="notice">
        <strong>기록할 내용</strong>
        <p>기기/OS, 카카오톡 버전, 권한창 표시 여부, 녹음·재생 성공 여부, MIME 형식과 확장자를 적어 김수경 님께 전달해 주세요.</p>
      </div>
    </section>
  );
}
