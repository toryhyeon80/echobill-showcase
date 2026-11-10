import { useCallback, useEffect, useImperativeHandle, useRef, useState, type Ref } from "react";
import { flushSync } from "react-dom";
import { RecordingOverlay } from "../ui/RecordingOverlay";
import { BottomSheet } from "../ui/BottomSheet";
import { Button } from "../ui/Button";

export interface VoiceRecorderHandle { start: () => void; retry: () => Promise<void> }
import { currentSessionEpoch, parseRecording } from "../api/client";
import type { ParseCandidate } from "../types/api";
import { describeApiFailure, type ApiFailure } from "../utils/apiError";
import { guardCandidateResponse, useAuthStore, useCandidateStore } from "../stores/appStore";
import { microphoneMessage, requestMicrophone } from "../utils/microphone";
import { prepareRecordingCue, recordingCueEnabled, type RecordingCue } from "../utils/recordingCue";
import { prepareGuidanceAudio } from "../utils/guidanceAudio";
import { autoStopEnabled } from "../utils/silenceDetection";
import { prepareSilenceMonitor, type SilenceMonitor } from "../utils/silenceMonitor";

interface Props {
  onCandidate: (candidate: ParseCandidate) => void;
  onFailure: (failure: ApiFailure) => void;
  onFallback: () => void;
  ensureAuth: () => Promise<void>;
  onBusy: (busy: boolean) => void;
  disabled: boolean;
  onAnswer?: (audio: Blob) => Promise<void>;
  controlRef?: Ref<VoiceRecorderHandle>;
}
const MAX_AUDIO_BYTES = 5_000_000;
/** Read again after async permission/audio work; the page can change in between. */
const pageHidden = () => document.visibilityState === "hidden";

/** Record in memory; short answers upload on stop, while standalone entry keeps preview. */
export function VoiceRecorder({ onCandidate, onFailure, onFallback, ensureAuth, onBusy, disabled, onAnswer, controlRef }: Props) {
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const lockRef = useRef<WakeLockSentinel | null>(null);
  const urlRef = useRef<string | null>(null);
  const mounted = useRef(true);
  const active = useRef(false);
  const permission = useRef<AbortController | null>(null);
  const cue = useRef<RecordingCue | null>(null);
  const silence = useRef<SilenceMonitor | null>(null);
  const attempt = useRef(0);
  const interrupted = useRef(false);
  const submitting = useRef(false);
  const autoUpload = useRef(false);
  const [recording, setRecording] = useState(false);
  const [starting, setStarting] = useState(false);
  const [showPermission, setShowPermission] = useState(false);
  const [readyToSpeak, setReadyToSpeak] = useState(false);
  const [audio, setAudio] = useState<Blob | null>(null);
  const [processing, setProcessing] = useState(false);
  const [seconds, setSeconds] = useState(0);
  const [failed, setFailed] = useState(false);
  const [retryCue, setRetryCue] = useState(false);
  const [autoStop, setAutoStop] = useState<"active" | "unavailable" | undefined>();
  useEffect(() => {
    if (audio && autoUpload.current) { autoUpload.current = false; void submit(); }
  }, [audio]);
  useImperativeHandle(controlRef, () => ({ start: () => { if (!disabled) void start(); }, retry: async () => { if (!disabled) await submit(); } }));
  useEffect(() => {
    if (!recording) return;
    const startedAt = Date.now();
    const timer = window.setInterval(() => setSeconds(Math.floor((Date.now() - startedAt) / 1000)), 1000);
    const deadline = window.setTimeout(stop, (onAnswer ? 8 : 30) * 1000);
    return () => { window.clearInterval(timer); window.clearTimeout(deadline); };
  }, [recording]);
  const [message, setMessage] = useState(onAnswer ? "말씀을 마치면 종료를 눌러 주세요." : "녹음 후 내용을 확인하고 후보를 만들 수 있습니다.");

  const stop = useCallback(() => {
    attempt.current += 1;
    permission.current?.abort();
    permission.current = null;
    cue.current?.cancel(); cue.current = null;
    silence.current?.cancel(); silence.current = null;
    active.current = false;
    if (recorderRef.current?.state === "recording") recorderRef.current.stop();
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    const lock = lockRef.current;
    lockRef.current = null;
    if (lock) void lock.release().catch(() => { if (mounted.current) setMessage("녹음을 중단했습니다. 화면 켜짐 유지가 해제되지 않았습니다."); });
    if (mounted.current) { setRecording(false); setStarting(false); setShowPermission(false); setReadyToSpeak(false); onBusy(false); }
  }, [onBusy]);

  useEffect(() => {
    mounted.current = true;
    const interrupt = () => {
      if (!active.current) return;
      interrupted.current = true;
      stop();
      if (controlRef) setFailed(true);
      setMessage("화면을 벗어나 녹음을 중단했습니다. 다시 녹음하거나 텍스트로 입력해 주세요.");
    };
    // Native permission prompts may hide the document before recording actually starts.
    const visibility = () => { if (document.visibilityState === "hidden" && streamRef.current) interrupt(); };
    document.addEventListener("visibilitychange", visibility);
    window.addEventListener("pagehide", interrupt);
    return () => {
      mounted.current = false;
      document.removeEventListener("visibilitychange", visibility);
      window.removeEventListener("pagehide", interrupt);
      stop();
      if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    };
  }, [stop]);

  function clearAudio() {
    setRetryCue(false);
    if (urlRef.current) URL.revokeObjectURL(urlRef.current);
    urlRef.current = null;
    setAudio(null);
  }

  async function start() {
    if (active.current || submitting.current) return;
    setRetryCue(false);
    prepareGuidanceAudio();
    if (!navigator.mediaDevices?.getUserMedia || typeof MediaRecorder === "undefined") {
      setMessage("녹음을 지원하지 않습니다. 텍스트로 입력해 주세요."); if (controlRef) setFailed(true); onFallback(); return;
    }
    active.current = true; interrupted.current = false; setStarting(true); setShowPermission(false); setReadyToSpeak(false); setFailed(false); setSeconds(0); onBusy(true);
    recorderRef.current = null;
    const request = ++attempt.current;
    const controller = new AbortController();
    permission.current = controller;
    // Unlock audio synchronously in the tap; microphone permission may take seconds.
    const startCue = prepareRecordingCue(recordingCueEnabled());
    cue.current = startCue;
    const silenceMonitor = !onAnswer && autoStopEnabled() ? prepareSilenceMonitor() : null;
    silence.current = silenceMonitor;
    setAutoStop(undefined);
    const permissionTimer = window.setTimeout(() => {
      if (request === attempt.current && mounted.current) setShowPermission(true);
    }, 500);
    autoUpload.current = !!controlRef || !!onAnswer;
    try {
      const stream = await requestMicrophone(controller.signal);
      window.clearTimeout(permissionTimer);
      if (request !== attempt.current || !mounted.current) {
        stream.getTracks().forEach((track) => track.stop()); return;
      }
      if (pageHidden()) {
        stream.getTracks().forEach((track) => track.stop());
        stop(); setFailed(true); setMessage("화면으로 돌아온 뒤 다시 녹음하거나 글자로 입력해 주세요.");
        onFallback(); return;
      }
      streamRef.current = stream;
      const mime = ["audio/webm;codecs=opus", "audio/mp4", "audio/webm"].find((value) => MediaRecorder.isTypeSupported(value));
      const recorder = new MediaRecorder(stream, mime ? { mimeType: mime } : undefined);
      recorderRef.current = recorder;
      const chunks: BlobPart[] = [];
      let bytes = 0;
      recorder.ondataavailable = (event) => {
        if (!mounted.current || recorderRef.current !== recorder || !event.data.size) return;
        bytes += event.data.size;
        if (bytes > MAX_AUDIO_BYTES) { interrupted.current = true; chunks.length = 0; stop(); setFailed(true); setMessage("녹음이 5MB를 넘었습니다. 더 짧게 녹음하거나 글자로 입력해 주세요."); onFallback(); return; }
        chunks.push(event.data);
      };
      recorder.onstop = () => {
        if (!mounted.current || recorderRef.current !== recorder) return;
        clearAudio();
        const blob = new Blob(chunks, { type: recorder.mimeType });
        if (blob.size && bytes <= MAX_AUDIO_BYTES && !interrupted.current) { urlRef.current = URL.createObjectURL(blob); setAudio(blob); }
        if (!interrupted.current) setMessage(blob.size ? onAnswer ? "음성 답을 확인하고 있어요." : "녹음을 확인한 뒤 후보 만들기를 눌러 주세요." : "녹음이 비어 있습니다. 다시 시도해 주세요.");
        if (!blob.size && !interrupted.current) { setFailed(true); onFallback(); }
        recorderRef.current = null;
      };
      recorder.onerror = () => {
        if (!mounted.current || recorderRef.current !== recorder) return;
        interrupted.current = true; stop(); setMessage("녹음에 실패했습니다. 텍스트로 입력해 주세요.");
        if (controlRef) setFailed(true);
        onFallback();
      };
      clearAudio();
      await startCue.play(controller.signal, () => {
        // Commit the listening screen before scheduling sound in this same task.
        flushSync(() => { setShowPermission(false); setReadyToSpeak(true); });
      });
      if (request !== attempt.current || !mounted.current) return;
      if (pageHidden()) {
        interrupted.current = true; stop(); setFailed(true);
        setMessage("화면으로 돌아온 뒤 다시 녹음하거나 글자로 입력해 주세요."); onFallback(); return;
      }
      cue.current = null; permission.current = null;
      recorder.start(1000); setStarting(false); setRecording(true);
      silenceMonitor?.start(stream, () => {
        if (request === attempt.current && mounted.current) stop();
      }, (available) => {
        if (request === attempt.current && mounted.current) setAutoStop(available ? "active" : "unavailable");
      });
      setMessage(onAnswer ? "말씀을 마치면 종료를 눌러 주세요." : "녹음 중입니다. 말씀을 마치면 종료해 주세요.");
      if (navigator.wakeLock) {
        try {
          const lock = await navigator.wakeLock.request("screen");
          if (active.current && mounted.current && recorderRef.current === recorder) lockRef.current = lock;
          else await lock.release();
        } catch { if (mounted.current && active.current) setMessage("녹음 중입니다. 화면을 켜 둔 채 종료해 주세요."); }
      }
    } catch (error) {
      if (request !== attempt.current || !mounted.current) return;
      stop();
      if (mounted.current) { setMessage(microphoneMessage(error)); setFailed(true); onFallback(); }
    } finally { window.clearTimeout(permissionTimer); }
  }

  async function submit() {
    if (!audio || submitting.current) return;
    submitting.current = true; setProcessing(true); onBusy(true);
    const initialOwner = useAuthStore.getState().userId;
    let owner = initialOwner;
    let epoch = currentSessionEpoch();
    const currentSession = () => owner === useAuthStore.getState().userId && epoch === currentSessionEpoch();
    const canRestore = guardCandidateResponse();
    try {
      await ensureAuth();
      // An initial login may establish the owner; replacing an existing session invalidates this audio.
      if ((initialOwner && !currentSession()) || !canRestore()) { if (mounted.current) clearAudio(); return; }
      owner = useAuthStore.getState().userId;
      epoch = currentSessionEpoch();
      if (onAnswer) {
        await onAnswer(audio);
        if (mounted.current) clearAudio();
        return;
      }
      const candidate = await parseRecording(audio);
      if (!currentSession() || !canRestore()) { if (mounted.current) clearAudio(); return; }
      if (mounted.current) { clearAudio(); onCandidate(candidate); }
      else useCandidateStore.getState().setCandidate(candidate);
    } catch (error) {
      if (!mounted.current) return;
      if (!currentSession() || !canRestore()) { clearAudio(); return; }
      const failure = describeApiFailure(error);
      const uncertain = ["network", "server", "pending", "auth", "rate"].includes(failure.kind);
      if (!uncertain) clearAudio();
      setMessage(failure.message);
      if (controlRef && failure.kind === "external" && failure.httpStatus !== 413) { setRetryCue(true); setFailed(true); }
      onFailure(failure);
      if (failure.kind === "input" || failure.kind === "external") onFallback();
    } finally {
      submitting.current = false;
      if (mounted.current) { setProcessing(false); onBusy(false); }
    }
  }

  if (controlRef && !onAnswer) return <>
    {(recording || showPermission || readyToSpeak || processing || failed) && <RecordingOverlay state={failed ? "failed" : readyToSpeak || recording ? "recording" : starting ? "requesting" : "processing"}
      autoStop={autoStop}
      retryCue={retryCue}
      preparing={starting && readyToSpeak}
      seconds={seconds} errorMessage={message} onStop={stop} onCancel={() => { interrupted.current = true; stop(); clearAudio(); setFailed(false); }}
      onRetry={() => void start()} onTypeInstead={() => { interrupted.current = true; stop(); clearAudio(); setFailed(false); onFallback(); }} />}
    <BottomSheet open={!!audio && !processing && !recording} title="녹음 내용 확인" onClose={clearAudio}
      footer={<Button disabled={disabled} onClick={() => void submit()}>녹음으로 후보 만들기</Button>}>
      <p role="status">{message}</p>
      {urlRef.current && <audio controls src={urlRef.current}>오디오 재생을 지원하지 않습니다.</audio>}
      <p className="mt-3 text-caption text-text-sub">전송 후에도 장부 확정 전에 내용을 확인할 수 있어요.</p>
    </BottomSheet>
  </>;
  return <section className="voice-card" aria-label="음성 녹음">
    <h2>{onAnswer ? "말로 답하기" : "말로 장부 기록하기"}</h2>
    <Button  className="recording-button" type="button" disabled={disabled || starting || processing} onClick={recording ? stop : () => void start()}>
      {processing ? "음성 확인 중…" : starting ? "마이크 여는 중…" : recording ? "녹음 종료" : "녹음 시작"}
    </Button>
    <p role="status">{message}</p>
    {starting && <button className="text-link" onClick={() => { interrupted.current = true; stop(); onFallback(); }}>취소하고 글자로 입력하기</button>}
    {!onAnswer && urlRef.current && <audio controls src={urlRef.current}>오디오 재생을 지원하지 않습니다.</audio>}
    {!onAnswer && audio && !recording && <>
      <Button  disabled={disabled || processing} onClick={() => void submit()}>{processing ? "음성 확인 중…" : onAnswer ? "음성 답 보내기" : "녹음으로 후보 만들기"}</Button>
      <button className="text-link" disabled={processing} onClick={clearAudio}>녹음 버리기</button>
    </>}
    {!onAnswer && <p className="muted">후보 만들기를 누르면 녹음을 전송합니다. 원음은 서버에 보관하지 않습니다. 장부 확정 전 내용을 확인해 주세요.</p>}
  </section>;
}
