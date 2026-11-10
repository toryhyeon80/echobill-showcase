"""Speech-or-not check on a recording before STT (PM 10/21, 거래처 힌트 안전장치 (b)).

With the counterparty hint on, gpt-4o-transcribe answered silence and steady noise with the owner's names
(10/21 noise check). Speech rises and falls syllable by syllable; silence is flat and quiet, and steady
noise or a tone is flat and loud. So a recording passes when it has at least one loud enough frame and
its frame levels spread over a wide enough range. Standard library only (16 kHz mono 16-bit wav from to_wav).
"""

import math
import struct
from array import array
from dataclasses import dataclass

FRAME_SAMPLES = 480  # 30 ms at 16 kHz
FLOOR_DBFS = -100.0
# Thresholds from the calibration in PR #187 (10/23, spread = 10th loudest frame − 10th percentile):
# 45 macOS TTS utterances (English — no Korean voice data here; the gate reads levels, not words) with 1 s
# pauses or 28 s after, clean or mixed with pink/brown noise. Sentences and two-word answers: spread
# 10.4-88 dB. Steady sound — the 7 run_stt_noise clips, 2-30 s pink/white/brown noise, a 30 s tone: 5.7 dB at
# most. 8 dB sits between. A one-word answer under loud brown noise can fall to 5.4 dB and be refused; this
# gate runs only on /parse-audio with the hint on (an entry needs more than one word), not on voice answers.
SILENCE_PEAK_DBFS = -45.0  # no 30 ms frame louder than this → silence
MIN_SPREAD_DB = 8.0  # level spread (10th loudest frame − 10th percentile) below this → steady noise or tone
# The top end is the LOUD_FRAMES-th loudest frame — a fixed count, not a share of the recording (PM #187 리뷰 ×2):
# - a 90th percentile is a pause frame when speech is under 10 % of the recording (2 s said, 30 s to the auto
#   stop) → real speech became a 503
# - a 99th percentile is the single loudest frame of a 2 s recording → silence plus one tap passed
# 10 frames = 0.3 s of loud sound, the same for any length; fewer frames than that is not speech.
LOUD_FRAMES = 10


@dataclass(frozen=True)
class Levels:
    """Frame-level summary of one recording (numbers only)."""

    frames: int
    peak_dbfs: float
    spread_db: float


def _pcm(wav: bytes) -> array:
    """16-bit samples of the wav's data chunk (ffmpeg's piped wav may carry a LIST chunk before it)."""
    at, data = 12, b""
    while at + 8 <= len(wav):
        chunk, size = wav[at:at + 4], struct.unpack("<I", wav[at + 4:at + 8])[0]
        if chunk == b"data":
            data = wav[at + 8:]  # a piped wav has size 0xFFFFFFFF — take everything after the header
            break
        at += 8 + size + (size % 2)
    samples = array("h")
    samples.frombytes(data[: len(data) - len(data) % 2])
    return samples


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, max(0, math.ceil(q * len(ordered)) - 1))]


def levels(wav: bytes) -> Levels:
    """dBFS of each 30 ms frame → peak and spread (LOUD_FRAMES-th loudest − 10th percentile; 0 when shorter)."""
    samples = _pcm(wav)
    frame_db = []
    for start in range(0, len(samples) - FRAME_SAMPLES + 1, FRAME_SAMPLES):
        frame = samples[start:start + FRAME_SAMPLES]
        rms = math.sqrt(sum(s * s for s in frame) / FRAME_SAMPLES)
        frame_db.append(20 * math.log10(rms / 32768) if rms > 0 else FLOOR_DBFS)
    if len(frame_db) < LOUD_FRAMES:
        return Levels(len(frame_db), max(frame_db, default=FLOOR_DBFS), 0.0)
    loud = sorted(frame_db, reverse=True)[LOUD_FRAMES - 1]
    return Levels(len(frame_db), max(frame_db), loud - _percentile(frame_db, 0.1))


def looks_like_speech(wav: bytes) -> bool:
    """False for silence (nothing above SILENCE_PEAK_DBFS) or steady sound (spread under MIN_SPREAD_DB)."""
    measured = levels(wav)
    return measured.peak_dbfs >= SILENCE_PEAK_DBFS and measured.spread_db >= MIN_SPREAD_DB
