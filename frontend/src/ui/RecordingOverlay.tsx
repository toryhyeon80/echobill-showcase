import { useEffect, useRef } from "react";
import { Keyboard, Square, X } from "lucide-react";
import { MicIcon } from "./MicIcon";
import { Button } from "./Button";
import { DotsSpinner } from "./DotsSpinner";
import { VoiceOrb } from "./VoiceOrb";
import { GuidanceSoundButton } from "../components/GuidanceSoundButton";
import { useGuidanceAudio } from "../utils/useGuidanceAudio";
import { stopGuidanceAudio } from "../utils/guidanceAudio";
import { useModalFocus } from "./useModalFocus";

export type RecordingState = "requesting" | "recording" | "processing" | "failed";

interface RecordingOverlayProps {
  state: RecordingState;
  /** Seconds since recording started (the page owns the timer). */
  seconds: number;
  /** Listening cue is visible, but the tone has not finished yet. */
  preparing?: boolean;
  autoStop?: "active" | "unavailable";
  /** Live mic level 0–1 for the voice orb (AnalyserNode RMS); omitted → breathing animation. */
  level?: number;
  /** Failed state message: mic denied, too quiet, STT 504 … */
  errorMessage?: string;
  /** Fixed retry audio is for STT failure, not permission/device/file errors. */
  retryCue?: boolean;
  onStop: () => void;
  /** 취소 while recording, X on the failure screen — both go back to 기록(홈). */
  onCancel: () => void;
  onRetry: () => void;
  /** Mic failure → text input (CLAUDE.md §4 녹음: 마이크 실패 → 텍스트 입력). */
  onTypeInstead: () => void;
}

/** One recording is at most 30 s (the 413 screen says "한 번에 30초까지"); the last 5 s show a countdown. */
const MAX_RECORD_SECONDS = 30;
const WARN_FROM_SECONDS = 25;

const clock = (s: number) => `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;

/**
 * Full-screen recording flow on top of 기록(홈): 듣는 중 → 정리하는 중 → (실패 시) 다시 말하기 / 글자로.
 * Full screen on purpose: one job at a time, no bottom tab to tap by mistake while speaking.
 * Text stays at the same height in every state (no jumping, DESIGN.md).
 * At MAX_RECORD_SECONDS the recording stops by itself, exactly as if 다 말했어요 was pressed (10/12 PM).
 */
export function RecordingOverlay({ state, seconds, preparing = false, autoStop, level, errorMessage, retryCue = false, onStop, onCancel, onRetry, onTypeInstead }: RecordingOverlayProps) {
  useGuidanceAudio("잘 못 들었어요. 한 번 더 말씀해 주세요.", "recording-failure", state === "failed" && retryCue, "/audio/stt-retry.mp3");
  const ref = useRef<HTMLDivElement>(null);
  useModalFocus(true, ref);
  const autoStopped = useRef(false);
  useEffect(() => {
    if (state !== "recording") {
      autoStopped.current = false; // ready for the next recording
      return;
    }
    if (seconds >= MAX_RECORD_SECONDS && !autoStopped.current) {
      autoStopped.current = true; // the page may re-render with the same seconds — stop once
      onStop();
    }
  }, [state, seconds, onStop]);
  const left = MAX_RECORD_SECONDS - seconds;
  const warn = state === "recording" && seconds >= WARN_FROM_SECONDS && left > 0;

  return (
    <div ref={ref} tabIndex={-1} role="dialog" aria-modal="true" aria-label="말로 기록하기" onClickCapture={stopGuidanceAudio} className="eb fixed inset-0 z-50 mx-auto flex max-w-md flex-col bg-surface px-page">
      {state === "failed" && <div className="absolute top-[calc(8px+env(safe-area-inset-top))] left-2"><GuidanceSoundButton /></div>}
      {/* Failure only: a way out without retrying or typing. Absolute, so the text below never moves. */}
      {(state === "failed" || state === "requesting") && (
        <button
          onClick={onCancel}
          aria-label="닫기"
          className="absolute top-[calc(8px+env(safe-area-inset-top))] right-2 flex h-12 w-12 items-center justify-center rounded-full text-text-sub active:bg-bg"
        >
          <X className="h-6 w-6" aria-hidden />
        </button>
      )}
      {/* Reserve 48px controls + 8px inset outside the scroller; keep the original title baseline. */}
      <main className="mt-[calc(56px+env(safe-area-inset-top))] flex min-h-0 flex-1 flex-col items-center overflow-y-auto pt-[max(0px,calc(18dvh-56px))] text-center [&>*]:shrink-0">
        <h1 className="text-title text-text-strong" aria-live="polite">
          {state === "requesting" ? "마이크를 연결하고 있어요" : state === "recording" ? "듣고 있어요" : state === "processing" ? "장부로 정리하고 있어요" : "잘 듣지 못했어요"}
        </h1>
        <p className="mt-space-2 min-h-[52px] text-body text-text-sub" aria-live="off">
          {state === "requesting" && "권한창에서 마이크 사용을 허용해 주세요. 앱에 따라 녹음할 때마다 허용을 물을 수 있어요. 글자로도 입력할 수 있어요."}
          {state === "recording" && <span key={seconds < 3 ? "name" : "details"} className="recording-hint">
            {seconds < 3 ? "가게 이름부터 또박또박 말해 주세요" : "거래처, 품목, 금액, 외상인지 말해 주세요"}
          </span>}
          {state === "processing" && "잠시만 기다려 주세요"}
          {state === "failed" && (errorMessage ?? "조용한 곳에서 다시 말하거나 글자로 적어 주세요")}
        </p>

        <div className="mt-10 flex h-40 w-40 items-center justify-center">
          {state === "recording" && (
            <VoiceOrb level={level} />
          )}
          {state === "processing" && <DotsSpinner label="정리하는 중" />}
          {state === "requesting" && <DotsSpinner label="마이크 연결 중" />}
          {state === "failed" && (
            <span className="flex h-28 w-28 items-center justify-center rounded-full bg-bg text-text-weak">
              <MicIcon className="h-12 w-12" />
            </span>
          )}
        </div>
        <p className={`mt-space-4 text-subtitle tabular-nums text-text-strong ${state === "recording" ? "" : "invisible"}`}>{clock(seconds)}</p>
        {state === "recording" && autoStop && <p className="mt-space-2 text-caption text-text-sub" aria-live="off">
          {autoStop === "active" ? "말을 마치고 잠시 조용해지면 녹음을 끝내요" : "자동 종료를 사용할 수 없어요. 다 말했어요를 눌러 주세요"}
        </p>}
        {/* Countdown line keeps its height so nothing moves; no extra colour (DESIGN.md 색 규칙) */}
        <p className={`mt-space-1 min-h-[26px] text-body text-text-sub ${warn ? "" : "invisible"}`} aria-live="polite">
          {warn && `${left}초 뒤 자동으로 끝나요`}
        </p>
      </main>

      <footer className="shrink-0 pb-[calc(16px+env(safe-area-inset-bottom))]">
        {state === "requesting" && <Button variant="secondary" onClick={onTypeInstead}><Keyboard className="h-5 w-5" aria-hidden /> 취소하고 글자로 입력하기</Button>}
        {state === "recording" && (
          // One row like the confirm screen: secondary 30% : primary 70% (DESIGN.md 버튼 체계)
          <div className="flex gap-space-2">
            <Button variant="secondary" className="!w-[30%]" onClick={onCancel}>
              취소
            </Button>
            <Button className="!w-[70%]" disabled={preparing} onClick={onStop}>
              <Square className="h-4 w-4 fill-current" aria-hidden /> 다 말했어요
            </Button>
          </div>
        )}
        {state === "processing" && <div className="h-14" aria-hidden />}
        {state === "failed" && (
          // Same one-row shape as the recording state: secondary 30% : primary 70%
          <div className="flex gap-space-2">
            <Button variant="secondary" className="!w-[30%]" aria-label="글자로 입력하기" onClick={onTypeInstead}>
              <Keyboard className="h-5 w-5" aria-hidden /> 글자로
            </Button>
            <Button className="!w-[70%]" onClick={onRetry}>
              <MicIcon className="h-5 w-5" /> 다시 말하기
            </Button>
          </div>
        )}
      </footer>
    </div>
  );
}
