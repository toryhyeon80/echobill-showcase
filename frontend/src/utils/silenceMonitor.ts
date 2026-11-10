import { createSilenceDetector } from "./silenceDetection";

export interface SilenceMonitor {
  start: (stream: MediaStream, onSilence: () => void, onAvailability: (available: boolean) => void) => void;
  cancel: () => void;
}

/** Unlock in the tap gesture; analyse in memory only, with no speaker output or recorded sample storage. */
export function prepareSilenceMonitor(): SilenceMonitor {
  let audio: AudioContext | null = null, source: MediaStreamAudioSourceNode | null = null, analyser: AnalyserNode | null = null;
  let timer: number | undefined;
  let resumeTimer: number | undefined;
  let settleReady: (() => void) | undefined;
  let cancelled = false;
  let started = false, observing = false;
  const close = () => {
    observing = false;
    window.clearTimeout(resumeTimer); resumeTimer = undefined;
    settleReady?.(); settleReady = undefined;
    window.clearInterval(timer); timer = undefined;
    source?.disconnect(); analyser?.disconnect(); source = null; analyser = null;
    if (audio && audio.state !== "closed") void audio.close().catch(() => undefined);
    audio = null;
  };
  let ready = Promise.resolve();
  try {
    const Audio = window.AudioContext ?? (window as Window & { webkitAudioContext?: typeof AudioContext }).webkitAudioContext;
    if (Audio) {
      audio = new Audio();
      const resumed = audio.resume().catch(close);
      ready = new Promise<void>((resolve) => {
        settleReady = resolve;
        resumeTimer = window.setTimeout(close, 500);
        void resumed.then(() => {
          window.clearTimeout(resumeTimer); resumeTimer = undefined;
          settleReady?.(); settleReady = undefined;
        });
      });
    }
  } catch { close(); }
  return {
    cancel: () => { cancelled = true; close(); },
    start: (stream, onSilence, onAvailability) => {
      if (started || cancelled) return;
      started = true;
      void ready.then(() => {
        if (cancelled) return;
        if (!audio || audio.state !== "running") { close(); onAvailability(false); return; }
        try {
          source = audio.createMediaStreamSource(stream); analyser = audio.createAnalyser(); analyser.fftSize = 2048;
          source.connect(analyser); // Deliberately no connection to audio.destination.
          const samples = new Float32Array(analyser.fftSize);
          const detect = createSilenceDetector(performance.now());
          onAvailability(true);
          observing = true;
          timer = window.setInterval(() => {
            if (!observing || cancelled) return;
            if (!audio || audio.state !== "running" || !analyser) { close(); if (!cancelled) onAvailability(false); return; }
            try {
              analyser.getFloatTimeDomainData(samples);
              const rms = Math.sqrt(samples.reduce((sum, sample) => sum + sample * sample, 0) / samples.length);
              if (detect(rms, performance.now())) { close(); if (!cancelled) onSilence(); }
            } catch { close(); if (!cancelled) onAvailability(false); }
          }, 100);
        } catch { close(); if (!cancelled) onAvailability(false); }
      });
    },
  };
}
