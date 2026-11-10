"""Measure real Whisper latency on a recording to pick STT_TIMEOUT_SECONDS (PM 추가 요청 1).

Calls the STT vendor directly with a generous 60s limit (no deadline) so the true spread shows,
then suggests a deadline. Needs OPENAI_API_KEY in backend/.env. Audio is sent, never stored.

    cd backend
    python -m scripts.measure_stt recording.m4a --runs 5

No recording at hand (any OS)? Generate the ~1-minute Korean ledger sample with OpenAI TTS
(≈ $0.01, ogg/opus like Android recordings), then measure it:
    python -m scripts.measure_stt --make-sample stt_sample.ogg
    python -m scripts.measure_stt stt_sample.ogg --runs 5

macOS alternative (iPhone-like audio/mp4):
    say -v Yuna -f sample.txt -o sample.m4a --file-format=m4af --data-format=aac
"""

import argparse
import mimetypes
import statistics
import sys
import time
from pathlib import Path

from openai import OpenAI

from app.config import get_settings


# ~1 minute of typical ledger speech (same sentences as the macOS sample)
SAMPLE_SENTENCES = (
    "김씨네 가게에 배추 세 박스 외상으로 삼만 원 줬어.",
    "박사장님한테 무 두 박스, 한 박스에 만 원씩 현금으로 받았고.",
    "이씨 상회 양파 오 킬로 외상 이만 오천 원.",
    "대박마트에 계란 두 판 사만 오천 원 외상.",
    "최씨네 대파 열 단 만 오천 원 카드로 받았어.",
)
SAMPLE_TEXT = " ".join(SAMPLE_SENTENCES * 4)


def make_sample(client: OpenAI, out: Path) -> Path:
    """Write the Korean sample as ogg/opus (a format Whisper accepts; Android records opus too)."""
    speech = client.audio.speech.create(model="tts-1", voice="alloy", input=SAMPLE_TEXT, response_format="opus")
    out.write_bytes(speech.read())
    return out


def percentile(values: list[float], pct: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, round(pct / 100 * len(ordered) + 0.5) - 1))]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="measure_stt", description=__doc__.splitlines()[0])
    parser.add_argument("audio", nargs="*", type=Path)
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--make-sample", type=Path, metavar="OUT.ogg", help="generate the Korean sample with TTS and exit")
    args = parser.parse_args(argv)
    if not args.audio and not args.make_sample:
        parser.error("측정할 녹음 파일 또는 --make-sample OUT.ogg 가 필요합니다")

    settings = get_settings()
    if not settings.openai_api_key or not settings.openai_api_key.get_secret_value():
        print("OPENAI_API_KEY가 없습니다 (backend/.env).")
        return 1
    client = OpenAI(api_key=settings.openai_api_key.get_secret_value(), timeout=60, max_retries=0)

    if args.make_sample:
        path = make_sample(client, args.make_sample)
        print(f"샘플 생성: {path} ({path.stat().st_size / 1e6:.2f}MB) — 이제 이 파일로 측정하세요")
        return 0

    times: list[float] = []
    for path in args.audio:
        audio = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "audio/mp4"
        for i in range(args.runs):
            started = time.perf_counter()
            client.audio.transcriptions.create(model=settings.stt_model, file=(path.name, audio, mime), language="ko")
            elapsed = time.perf_counter() - started
            times.append(elapsed)
            print(f"{path.name} ({len(audio) / 1e6:.2f}MB) run {i + 1}: {elapsed:.2f}s")

    p50, p95 = statistics.median(times), percentile(times, 95)
    suggest = max(5, round(p95 * 1.5 + 0.49))
    budget = suggest + settings.llm_timeout_seconds
    print(f"\np50 {p50:.2f}s · p95 {p95:.2f}s · max {max(times):.2f}s (n={len(times)})")
    print(f"현재 STT_TIMEOUT_SECONDS={settings.stt_timeout_seconds:g} · 제안 ≈ {suggest}s (p95×1.5)")
    print(f"제안값 + LLM {settings.llm_timeout_seconds:g}s = {budget:g}s → FE 요청 타임아웃(15s)보다 {'짧음 ✅' if budget < 15 else '김 ❌'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
