import { test } from "node:test";
import assert from "node:assert/strict";
import { autoStopEnabled, createSilenceDetector, saveAutoStopEnabled } from "../src/utils/silenceDetection";
import { prepareSilenceMonitor } from "../src/utils/silenceMonitor";
import "./review-fixture";

test("automatic stop is opt-in; initial silence and short noise bursts never end a recording", () => {
  localStorage.clear(); assert.equal(autoStopEnabled(), false);
  saveAutoStopEnabled(true); assert.equal(autoStopEnabled(), true);
  saveAutoStopEnabled(false); assert.equal(autoStopEnabled(), false);
  const silence = createSilenceDetector(0);
  for (let now = 0; now <= 30_000; now += 100) assert.equal(silence(0, now), false);
  const noise = createSilenceDetector(0);
  for (let now = 0; now <= 10_000; now += 100) assert.equal(noise(now < 200 ? 0.1 : 0, now), false);
});

test("speech must precede silence; pauses resume and only one final stop is emitted", () => {
  const detect = createSilenceDetector(0);
  for (let now = 0; now <= 1000; now += 100) assert.equal(detect(0.1, now), false);
  assert.equal(detect(0, 2000), false); assert.equal(detect(0, 3900), false);
  assert.equal(detect(0.1, 3999), false, "speech after a short pause resets the silence timer");
  assert.equal(detect(0, 6998), false); assert.equal(detect(0, 6999), true);
  assert.equal(detect(0, 8000), false);
  assert.equal(createSilenceDetector(100)(NaN, 200), false);
});

class AudioFixture {
  static instances: AudioFixture[] = [];
  static mode = "running";
  static rms = 0;
  static samplingError = false;
  state = "suspended";
  destination = {};
  connected = false;
  disconnected = false;
  constructor() { AudioFixture.instances.push(this); }
  resume() { if (AudioFixture.mode === "blocked") return new Promise<void>(() => {}); this.state = "running"; return Promise.resolve(); }
  close() { this.state = "closed"; return Promise.resolve(); }
  createMediaStreamSource() {
    return { connect: (target: unknown) => { assert.notEqual(target, this.destination); this.connected = true; }, disconnect: () => { this.disconnected = true; } };
  }
  createAnalyser() {
    return { fftSize: 2048, disconnect() {}, getFloatTimeDomainData(samples: Float32Array) {
      if (AudioFixture.samplingError) throw new Error("audio interrupted"); samples.fill(AudioFixture.rms);
    } };
  }
}

for (const mode of ["silence", "suspended", "sampling-error", "cancelled", "blocked-cancelled", "blocked", "unsupported"] as const) {
  test(`audio monitor ${mode}: releases audio and safely preserves manual recording`, async (t) => {
    const oldPerformance = Object.getOwnPropertyDescriptor(globalThis, "performance");
    const oldAudio = window.AudioContext;
    const timers = { setTimeout: window.setTimeout, clearTimeout: window.clearTimeout, setInterval: window.setInterval, clearInterval: window.clearInterval };
    let now = 0, stops = 0;
    const available: boolean[] = [];
    AudioFixture.instances = []; AudioFixture.mode = mode.startsWith("blocked") ? "blocked" : "running";
    AudioFixture.rms = 0.1; AudioFixture.samplingError = false;
    Object.assign(window, { AudioContext: mode === "unsupported" ? undefined : AudioFixture });
    Object.defineProperty(globalThis, "performance", { configurable: true, value: { now: () => now } });
    t.mock.timers.enable({ apis: ["setTimeout", "setInterval"] });
    Object.assign(window, { setTimeout, clearTimeout, setInterval, clearInterval });
    const monitor = prepareSilenceMonitor();
    const tick = (duration: number) => { for (let i = 0; i < duration; i += 100) { now += 100; t.mock.timers.tick(100); } };
    try {
      if (mode.endsWith("cancelled")) monitor.cancel();
      monitor.start({} as MediaStream, () => { stops++; }, (value) => available.push(value));
      await new Promise<void>((resolve) => setImmediate(resolve));
      if (mode === "blocked") { tick(500); await new Promise<void>((resolve) => setImmediate(resolve)); }
      if (["blocked", "unsupported"].includes(mode)) assert.deepEqual(available, [false]);
      else if (mode.endsWith("cancelled")) assert.deepEqual(available, []);
      else {
        assert.deepEqual(available, [true]);
        tick(1000);
        if (mode === "suspended") AudioFixture.instances[0].state = "suspended";
        if (mode === "sampling-error") AudioFixture.samplingError = true;
        AudioFixture.rms = 0; tick(5000);
        assert.equal(stops, mode === "silence" ? 1 : 0);
        if (mode !== "silence") assert.deepEqual(available, [true, false]);
      }
      monitor.cancel(); tick(5000);
      assert.equal(stops, mode === "silence" ? 1 : 0);
      for (const audio of AudioFixture.instances) assert.equal(audio.state, "closed");
    } finally {
      monitor.cancel(); Object.assign(window, timers, { AudioContext: oldAudio });
      t.mock.timers.reset();
      if (oldPerformance) Object.defineProperty(globalThis, "performance", oldPerformance);
    }
  });
}
