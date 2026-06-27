"""Wrap raw PCM (16 kHz mono s16le) in a WAV container for archival."""

from __future__ import annotations

import io
import wave


def wrap_pcm(pcm: bytes, *, rate: int = 16000, channels: int = 1, sampwidth: int = 2) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(rate)
        w.writeframes(pcm)
    return buf.getvalue()


# bytes per second of 16 kHz mono s16le audio (used for time-based chunk rotation)
BYTES_PER_SECOND = 16000 * 2
