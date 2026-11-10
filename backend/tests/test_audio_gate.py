"""PM 10/21 (b) — with the counterparty hint on, silence and steady noise never reach STT."""

import io
import math
import random
import wave
from types import SimpleNamespace

import pytest

from app.config import Settings
from app.services import stt
from app.services.audio_convert import AudioConvertError
from app.services.audio_gate import levels, looks_like_speech

RATE = 16_000


def _wav(samples: list[float]) -> bytes:
    """16 kHz mono 16-bit wav from samples in -1..1."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as out:
        out.setnchannels(1)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(b"".join(int(max(-1, min(1, x)) * 32767).to_bytes(2, "little", signed=True) for x in samples))
    return buffer.getvalue()


def _tone(seconds: float, amp: float, hz: float = 220) -> list[float]:
    return [amp * math.sin(2 * math.pi * hz * i / RATE) for i in range(int(seconds * RATE))]


def _noise(seconds: float, amp: float, seed: int = 1) -> list[float]:
    rng = random.Random(seed)
    return [amp * rng.uniform(-1, 1) for _ in range(int(seconds * RATE))]


def _syllables(amp: float, floor: float = 0.0, *, bursts: int = 6, after: float = 1.0) -> list[float]:
    """Speech-like: 1 s pause, `bursts` × (150 ms burst + 100 ms gap), `after` s pause, over an optional noise floor."""
    gap = [0.0] * int(0.1 * RATE)
    voiced = [0.0] * RATE
    for _ in range(bursts):
        voiced += _tone(0.15, amp) + gap
    voiced += [0.0] * int(after * RATE)
    return [x + n for x, n in zip(voiced, _noise(len(voiced) / RATE, floor))]


@pytest.mark.parametrize("name,samples,speech", [
    ("silence", [0.0] * (2 * RATE), False),
    ("steady loud noise", _noise(1.5, 0.3), False),
    ("steady quiet noise", _noise(2, 0.002), False),
    ("steady tone", _tone(1, 0.01), False),
    ("speech-like", _syllables(0.3), True),
    ("speech-like in noise", _syllables(0.3, floor=0.03), True),
    ("speech-like but whisper-quiet", _syllables(0.003), False),
    # PM #187 리뷰: a short word, then the recording runs on to the 30 s auto stop — speech under 10 % of it
    ("short word then a long pause", _syllables(0.3, bursts=2, after=28), True),
    ("short word then a long pause in noise", _syllables(0.3, floor=0.03, bursts=2, after=28), True),
    # PM #187 리뷰 2: one phone tap in a short silent recording is not speech (it was, with the 99th percentile)
    ("2 s silence + one tap", [0.0] * RATE + _tone(0.02, 0.8, hz=2000) + [0.0] * RATE, False),
    ("10 s silence + one tap", [0.0] * (5 * RATE) + _tone(0.02, 0.8, hz=2000) + [0.0] * (5 * RATE), False),
    ("2 s silence + three taps", ([0.0] * (RATE // 2) + _tone(0.02, 0.8, hz=2000)) * 3 + [0.0] * (RATE // 2), False),
])
def test_speech_or_not(name: str, samples: list[float], speech: bool) -> None:
    assert looks_like_speech(_wav(samples)) is speech, (name, levels(_wav(samples)))


def test_empty_wav_is_not_speech() -> None:
    assert levels(_wav([])).frames == 0
    assert not looks_like_speech(_wav([]))


class _Transcriptions:
    def __init__(self) -> None:
        self.calls = 0

    def create(self, **_kwargs):
        self.calls += 1
        return SimpleNamespace(text="가나상회 배추 두 박스")


def _call(audio: bytes, hints: list[str], monkeypatch: pytest.MonkeyPatch, to_wav=None) -> _Transcriptions:
    monkeypatch.setattr(stt, "to_wav", to_wav or (lambda data, timeout: data))  # the test audio is wav already
    t = _Transcriptions()
    settings = Settings(_env_file=None, app_env="development", openai_api_key="test-key")
    stt.transcribe(audio, "a.webm", "audio/webm", counterparty_hints=hints, settings=settings,
                   client=SimpleNamespace(audio=SimpleNamespace(transcriptions=t)))
    return t


def test_with_hints_silence_is_refused_before_any_vendor_call(monkeypatch: pytest.MonkeyPatch) -> None:
    t = _Transcriptions()
    monkeypatch.setattr(stt, "to_wav", lambda data, timeout: data)
    with pytest.raises(stt.STTError):
        stt.transcribe(_wav([0.0] * RATE), "a.webm", "audio/webm", counterparty_hints=["가나상회"],
                       settings=Settings(_env_file=None, app_env="development", openai_api_key="test-key"),
                       client=SimpleNamespace(audio=SimpleNamespace(transcriptions=t)))
    assert t.calls == 0


def test_with_hints_speech_goes_through(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _call(_wav(_syllables(0.3)), ["가나상회"], monkeypatch).calls == 1


def test_without_hints_the_gate_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    # hints off (the default) → the exact path as before, silence included
    assert _call(_wav([0.0] * RATE), [], monkeypatch).calls == 1


def test_an_unreadable_recording_skips_the_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(data, timeout):
        raise AudioConvertError("ffmpeg exit=1")

    assert _call(b"not audio", ["가나상회"], monkeypatch, to_wav=broken).calls == 1
