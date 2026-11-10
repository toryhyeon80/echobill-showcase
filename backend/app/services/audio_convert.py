"""Recording format conversion for STT vendors that do not take browser formats (webm · mp4)."""

import os
import subprocess
import tempfile
from pathlib import Path

import imageio_ffmpeg

WAV_HEADER_BYTES = 44  # a wav with no samples — what ffmpeg leaves when it could not read the input


class AudioConvertError(RuntimeError):
    """The recording could not be turned into wav — the caller answers like an STT failure (503)."""


def _scratch_dir() -> str | None:
    """Memory-backed /dev/shm when the container has it (Railway Linux), else the default temp dir."""
    shm = "/dev/shm"
    return shm if os.path.isdir(shm) and os.access(shm, os.W_OK) else None


def to_wav(audio: bytes, *, timeout: float) -> bytes:
    """
    Convert a browser recording (webm/opus, mp4/aac …) to 16 kHz mono wav and return the bytes.

    iPhone mp4 can keep its index at the end of the file, which ffmpeg cannot read from a pipe
    (it then writes an empty wav and still exits 0). So the input goes to a private temp dir
    (memory-backed /dev/shm when available) that is removed before this returns — the recording
    is never kept (CLAUDE.md §4-10). Output comes back over a pipe.
    """
    try:
        exe = imageio_ffmpeg.get_ffmpeg_exe()
        with tempfile.TemporaryDirectory(dir=_scratch_dir()) as scratch:
            source = Path(scratch) / "in"
            source.write_bytes(audio)
            result = subprocess.run(
                [exe, "-nostdin", "-hide_banner", "-loglevel", "error", "-i", str(source),
                 "-ac", "1", "-ar", "16000", "-f", "wav", "pipe:1"],
                capture_output=True, timeout=timeout, check=False,
            )
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
        raise AudioConvertError(type(exc).__name__) from exc
    if result.returncode != 0 or len(result.stdout) <= WAV_HEADER_BYTES:
        raise AudioConvertError(f"ffmpeg exit={result.returncode} bytes={len(result.stdout)}")
    return result.stdout
