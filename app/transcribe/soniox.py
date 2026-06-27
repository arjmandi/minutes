"""Real-time Soniox STT client. Protocol: docs/soniox-rt-protocol.md.

Connects to the RT WebSocket, sends the JSON config first, forwards PCM as binary frames, and
yields transcript events. Translation is OFF. Per-token ``is_final`` + ``language`` drive
segmentation; ``<end>`` (endpoint) and ``<fin>`` (manual finalize) mark boundaries. On graceful
end of audio we send a manual ``finalize`` (to flush the trailing utterance) then the empty
end-of-audio frame.

Live-validate (docs §7): confirm Persian (fa) works on stt-rt-v5.
TODO(follow-up): on a recoverable 503/connection-cap error, reconnect and continue from the same
audio iterator (preserving final_tokens) instead of failing the call (currently surfaced as a
recoverable SonioxError). Proactive recycle before the 300-min cap also belongs here.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator

import websockets

from app.logging import get_logger
from app.transcribe.base import FinalSegment, Interim, TranscriptEvent

log = get_logger("soniox")

_URL = "wss://stt-rt.soniox.com/transcribe-websocket"
_MODEL = "stt-rt-v5"
_KEEPALIVE_S = 15.0  # must stay under the 20s idle timeout
_SEGMENT_MARKERS = {"<end>", "<fin>"}
_RECOVERABLE_TYPES = {"service_unavailable", "temp_api_key_session_expired"}


class SonioxError(RuntimeError):
    def __init__(self, message: str, *, recoverable: bool = False) -> None:
        super().__init__(message)
        self.recoverable = recoverable


class SonioxTranscriber:
    def __init__(
        self,
        *,
        api_key: str,
        language_hints: list[str],
        vocabulary: list[str] | None = None,
        model: str = _MODEL,
        url: str = _URL,
    ) -> None:
        self._api_key = api_key
        self._language_hints = language_hints
        self._vocabulary = vocabulary or []
        self._model = model
        self._url = url

    def _config(self) -> dict:
        config: dict = {
            "api_key": self._api_key,
            "model": self._model,
            "audio_format": "pcm_s16le",
            "sample_rate": 16000,
            "num_channels": 1,
            "language_hints": self._language_hints,
            "enable_language_identification": True,
            "enable_endpoint_detection": True,
        }
        if self._vocabulary:
            config["context"] = {"terms": self._vocabulary}
        return config  # `translation` intentionally omitted => translation OFF

    async def stream(self, audio: AsyncIterator[bytes]) -> AsyncIterator[TranscriptEvent]:
        last_audio = [time.monotonic()]
        async with websockets.connect(self._url, max_size=None) as ws:
            await ws.send(json.dumps(self._config()))

            async def sender() -> None:
                try:
                    async for chunk in audio:
                        last_audio[0] = time.monotonic()
                        await ws.send(chunk)  # binary frame = audio (already real-time paced)
                except asyncio.CancelledError:
                    raise  # cancelled teardown: do NOT send end-of-audio markers
                else:
                    # Graceful exhaustion: flush the trailing utterance, then end-of-audio.
                    try:
                        await ws.send(json.dumps({"type": "finalize"}))
                        await ws.send(b"")
                    except websockets.ConnectionClosed:
                        pass

            async def keepalive() -> None:
                try:
                    while True:
                        await asyncio.sleep(_KEEPALIVE_S)
                        if time.monotonic() - last_audio[0] >= _KEEPALIVE_S:
                            await ws.send(json.dumps({"type": "keepalive"}))
                except (asyncio.CancelledError, websockets.ConnectionClosed):
                    pass

            send_task = asyncio.create_task(sender())
            ka_task = asyncio.create_task(keepalive())
            final_tokens: list[dict] = []
            seg_no = 0
            try:
                async for raw in ws:
                    msg = json.loads(raw)
                    if msg.get("error_code"):
                        etype = msg.get("error_type")
                        raise SonioxError(
                            f"{msg.get('error_code')} {etype}: {msg.get('error_message')}",
                            recoverable=etype in _RECOVERABLE_TYPES,
                        )
                    non_final: list[dict] = []
                    for tok in msg.get("tokens", []):
                        text = tok.get("text", "")
                        if text in _SEGMENT_MARKERS:
                            seg_no += 1
                            seg = _segment(final_tokens, f"u{seg_no}")
                            final_tokens = []
                            if seg is not None:
                                yield seg
                            continue
                        if tok.get("is_final"):
                            final_tokens.append(tok)
                        else:
                            non_final.append(tok)
                    if non_final:
                        # Full current-segment hypothesis: confirmed prefix + provisional tail.
                        hypothesis = (
                            "".join(t.get("text", "") for t in final_tokens)
                            + "".join(t.get("text", "") for t in non_final)
                        ).strip()
                        if hypothesis:
                            yield Interim(text=hypothesis)
                    if msg.get("finished"):
                        break
                seg_no += 1
                seg = _segment(final_tokens, f"u{seg_no}")
                if seg is not None:
                    yield seg
            finally:
                ka_task.cancel()
                send_task.cancel()
                await asyncio.gather(ka_task, send_task, return_exceptions=True)


def _segment(tokens: list[dict], utterance_id: str) -> FinalSegment | None:
    text = "".join(t.get("text", "") for t in tokens).strip()
    if not text:
        return None
    languages = [t.get("language") for t in tokens if t.get("language")]
    starts = [t["start_ms"] for t in tokens if t.get("start_ms") is not None]
    ends = [t["end_ms"] for t in tokens if t.get("end_ms") is not None]
    return FinalSegment(
        text=text,
        utterance_id=utterance_id,
        language=languages[-1] if languages else None,
        start_ms=int(min(starts)) if starts else None,
        end_ms=int(max(ends)) if ends else None,
    )
