"""Stream a 16 kHz mono PCM WAV through the ingest WebSocket — an end-to-end pipeline test.

This is the same contract the browser capture extension uses (JSON hello -> framed PCM -> end), so
it exercises the whole deployed path: TLS edge -> auth -> admission -> Soniox -> persistence ->
translation -> live fan-out. Watch the result in the viewer (/app) or GET /meetings/<id>/transcript.

Convert any audio to the required format first:
    ffmpeg -i input.m4a -ar 16000 -ac 1 -f wav sample16k.wav

Run (token via --token or the MINUTES_TOKEN env var):
    MINUTES_TOKEN=<token> uv run python scripts/ingest_wav.py \
        wss://minutes.freddiespirit.com/ingest sample16k.wav --meeting demo-001
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import wave
from pathlib import Path

import websockets

# make the repo root importable so `app.*` resolves when run directly
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.audio.frames import encode_frame  # noqa: E402

_RATE = 16000
_SAMPLES_PER_FRAME = 320  # 20 ms at 16 kHz
_FRAME_MS = 20


async def stream(url: str, wav_path: str, platform: str, meeting: str, token: str) -> None:
    wav = wave.open(wav_path, "rb")
    if (wav.getframerate(), wav.getnchannels(), wav.getsampwidth()) != (_RATE, 1, 2):
        raise SystemExit(
            f"need 16kHz mono s16le WAV; got rate={wav.getframerate()} "
            f"channels={wav.getnchannels()} width={wav.getsampwidth()}. "
            "Convert: ffmpeg -i in.wav -ar 16000 -ac 1 -f wav out.wav"
        )
    call_id = f"{meeting}-{os.getpid()}"
    async with websockets.connect(
        url, subprotocols=["minutes.auth.bearer", token], max_size=None
    ) as ws:
        await ws.send(
            json.dumps(
                {
                    "type": "hello",
                    "platform": platform,
                    "external_meeting_id": meeting,
                    "call_id": call_id,
                }
            )
        )
        print("hello ->", await ws.recv())

        seq = 0
        ts = 0
        sent = 0
        while True:
            pcm = wav.readframes(_SAMPLES_PER_FRAME)
            if len(pcm) < _SAMPLES_PER_FRAME * 2:
                break  # drop the trailing partial frame
            await ws.send(encode_frame(seq, ts, pcm))
            seq += 1
            ts += _FRAME_MS
            sent += 1
            await asyncio.sleep(_FRAME_MS / 1000)  # pace ~real-time so Soniox sees a live stream
        print(f"streamed {sent} frames (~{sent * _FRAME_MS / 1000:.1f}s); sending end")
        await ws.send(json.dumps({"type": "end"}))
        try:
            while True:
                print("server ->", await asyncio.wait_for(ws.recv(), timeout=15))
        except (TimeoutError, websockets.ConnectionClosed):
            pass


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("url", help="wss://<host>/ingest")
    ap.add_argument("wav", help="16 kHz mono s16le WAV file")
    ap.add_argument("--platform", default="meet")
    ap.add_argument("--meeting", default="demo-001", help="external_meeting_id")
    ap.add_argument("--token", default=os.environ.get("MINUTES_TOKEN", ""))
    args = ap.parse_args()
    if not args.token:
        raise SystemExit("provide a capability token via --token or MINUTES_TOKEN")
    asyncio.run(stream(args.url, args.wav, args.platform, args.meeting, args.token))


if __name__ == "__main__":
    main()
