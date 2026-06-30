"""Real-time Soniox STT client. Protocol: docs/soniox-rt-protocol.md.

Connects to the RT WebSocket, sends the JSON config first, forwards PCM as binary frames, and
yields transcript events. Translation is OFF. Per-token ``is_final`` + ``language`` drive
segmentation; ``<end>`` (endpoint) and ``<fin>`` (manual finalize) mark boundaries. On graceful
end of audio we send a manual ``finalize`` (to flush the trailing utterance) then the empty
end-of-audio frame.

Resilience (Phase 0): a single ``stream()`` call spans MULTIPLE Soniox connections. On a transient
failure — a recoverable error type, an abnormal close, or a connect error — it reconnects with
capped exponential backoff and continues from the SAME audio iterator, preserving the in-progress
segment (``final_tokens``/``seg_no``) so no finalized segment is lost. It also proactively recycles
the connection at a clean segment boundary before Soniox's ~300-min single-connection cap. Hard
errors (bad/expired key, insufficient credit, quota/plan limits, malformed config) are NOT
recoverable: ``stream()`` raises ``SonioxError`` so the caller can surface "check your Soniox
plan / top up" to the user. A drop AFTER audio is fully sent ends the stream with what we have.
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
# Error types Soniox may emit that are worth retrying (transient capacity / short-lived key).
_RECOVERABLE_TYPES = {"service_unavailable", "temp_api_key_session_expired"}
# Reconnect policy (consecutive-failure based; a connection that lives a while resets the counter).
_MAX_RECONNECTS = 6
_RESET_AFTER_S = 30.0
_BACKOFF_BASE_S = 0.5
_BACKOFF_MAX_S = 8.0
# Proactively recycle before Soniox's ~300-min single-connection cap (at a clean segment boundary,
# with a hard fallback so a long continuous utterance can't run past the cap).
_RECYCLE_S = 290 * 60
_HARD_RECYCLE_S = 298 * 60


class SonioxError(RuntimeError):
    def __init__(self, message: str, *, recoverable: bool = False) -> None:
        super().__init__(message)
        self.recoverable = recoverable


class _Recycle(Exception):
    """Internal signal: this connection hit the recycle age — reconnect to continue cleanly."""


class _Dropped(Exception):
    """Internal signal: the connection ended before the audio was finished — reconnect."""


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
        """Yield transcript events across one or more Soniox connections (reconnect/recycle)."""
        ait = audio.__aiter__()
        # Shared across reconnects so the in-progress segment + numbering survive a connection swap.
        state: dict = {"final_tokens": [], "seg_no": 0, "audio_done": False}
        attempt = 0
        while True:
            conn_started = time.monotonic()
            try:
                async for ev in self._run_connection(ait, state):
                    yield ev
                return  # audio exhausted + server finished: clean end of the whole stream
            except _Recycle:
                if state["audio_done"]:
                    return
                attempt = 0  # a recycle is not a failure
                log.info("soniox.recycle", seg_no=state["seg_no"])
                continue
            except (_Dropped, websockets.ConnectionClosed, OSError, SonioxError) as exc:
                if state["audio_done"]:
                    return  # audio already fully sent; nothing more to transcribe
                recoverable = not isinstance(exc, SonioxError) or exc.recoverable
                if time.monotonic() - conn_started >= _RESET_AFTER_S:
                    attempt = 0  # the connection worked a while -> treat as a fresh transient blip
                if not recoverable or attempt >= _MAX_RECONNECTS:
                    raise  # terminal: bad/expired key, no credit, quota, or too many failures
                attempt += 1
                delay = min(_BACKOFF_BASE_S * 2 ** (attempt - 1), _BACKOFF_MAX_S)
                log.warning("soniox.reconnect", attempt=attempt, delay=delay, reason=repr(exc))
                await asyncio.sleep(delay)
                continue

    async def _run_connection(
        self, ait: AsyncIterator[bytes], state: dict
    ) -> AsyncIterator[TranscriptEvent]:
        last_audio = [time.monotonic()]
        conn_started = time.monotonic()
        async with websockets.connect(self._url, max_size=None) as ws:
            await ws.send(json.dumps(self._config()))

            async def sender() -> None:
                try:
                    while True:
                        try:
                            chunk = await ait.__anext__()
                        except StopAsyncIteration:
                            break
                        last_audio[0] = time.monotonic()
                        await ws.send(chunk)  # binary frame = audio (already real-time paced)
                except asyncio.CancelledError:
                    raise  # cancelled teardown / reconnect: do NOT send end-of-audio markers
                else:
                    # Graceful exhaustion: flush the trailing utterance, then end-of-audio.
                    # End-of-stream is an empty TEXT frame ("") per Soniox's example.
                    state["audio_done"] = True
                    try:
                        await ws.send(json.dumps({"type": "finalize"}))
                        await ws.send("")
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
            finished = False
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
                            state["seg_no"] += 1
                            seg = _segment(state["final_tokens"], f"u{state['seg_no']}")
                            state["final_tokens"] = []
                            if seg is not None:
                                yield seg
                            # Recycle at a clean boundary (no in-progress segment to lose).
                            if (
                                not state["audio_done"]
                                and time.monotonic() - conn_started >= _RECYCLE_S
                            ):
                                raise _Recycle
                            continue
                        if tok.get("is_final"):
                            state["final_tokens"].append(tok)
                        else:
                            non_final.append(tok)
                    if non_final:
                        # Full current-segment hypothesis: confirmed prefix + provisional tail.
                        hypothesis = (
                            "".join(t.get("text", "") for t in state["final_tokens"])
                            + "".join(t.get("text", "") for t in non_final)
                        ).strip()
                        if hypothesis:
                            yield Interim(text=hypothesis)
                    if msg.get("finished"):
                        finished = True
                        break
                    # Hard safety: never run a single connection past the cap, even mid-utterance.
                    if (
                        not state["audio_done"]
                        and time.monotonic() - conn_started >= _HARD_RECYCLE_S
                    ):
                        raise _Recycle
                if not finished and not state["audio_done"]:
                    # The socket iterator ended before `finished` and before we sent end-of-audio:
                    # an abnormal server-side close -> reconnect and continue.
                    raise _Dropped("connection ended before audio finished")
                # Clean finish: flush the trailing segment.
                state["seg_no"] += 1
                seg = _segment(state["final_tokens"], f"u{state['seg_no']}")
                state["final_tokens"] = []
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
