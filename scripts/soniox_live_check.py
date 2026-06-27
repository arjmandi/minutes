"""Live Soniox validation: stream a 16 kHz mono s16le WAV through SonioxTranscriber.

    uv run python scripts/soniox_live_check.py <wav_path> [lang_hints_csv]

Requires MINUTES_SONIOX_API_KEY (env or .env). Prints interim + final events; makes a real
(billable) Soniox connection. Dev tool — not part of the test suite.
"""

from __future__ import annotations

import asyncio
import sys
import wave

from app.config import get_settings
from app.transcribe.base import FinalSegment, Interim
from app.transcribe.soniox import SonioxTranscriber

_FRAMES_PER_CHUNK = 1600  # 100 ms @ 16 kHz mono (2 bytes/frame -> 3200 bytes)


async def _audio_from_wav(path: str):
    wf = wave.open(path, "rb")
    rate, ch, width = wf.getframerate(), wf.getnchannels(), wf.getsampwidth()
    if (rate, ch, width) != (16000, 1, 2):
        raise SystemExit(f"need 16kHz mono s16le; got {rate}Hz {ch}ch {width * 8}bit")
    try:
        while True:
            frames = wf.readframes(_FRAMES_PER_CHUNK)
            if not frames:
                return
            yield frames
            await asyncio.sleep(0.1)  # pace ~real-time, like live capture
    finally:
        wf.close()


async def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("usage: soniox_live_check.py <wav_path> [lang_hints_csv]")
    path = sys.argv[1]
    hints = sys.argv[2].split(",") if len(sys.argv) > 2 else ["en", "de", "fa"]
    settings = get_settings()
    if not settings.soniox_api_key:
        raise SystemExit("MINUTES_SONIOX_API_KEY not set")

    transcriber = SonioxTranscriber(api_key=settings.soniox_api_key, language_hints=hints)
    finals: list[FinalSegment] = []
    print(f"streaming {path} with language_hints={hints} ...")
    async for event in transcriber.stream(_audio_from_wav(path)):
        if isinstance(event, Interim):
            print(f"  ~ {event.text}")
        elif isinstance(event, FinalSegment):
            print(
                f"  FINAL [{event.language}] {event.text!r} "
                f"({event.start_ms}-{event.end_ms}ms) id={event.utterance_id}"
            )
            finals.append(event)
    langs = sorted({f.language for f in finals if f.language})
    print(f"== {len(finals)} final segment(s); languages seen: {langs}")


if __name__ == "__main__":
    asyncio.run(main())
