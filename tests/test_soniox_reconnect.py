"""Phase-0 resilience for the real-time Soniox client: one stream() spans multiple connections.

Mocks websockets.connect with scripted frames to exercise reconnect/recycle without a real Soniox
endpoint. Verifies: a clean stream; a recoverable error mid-stream reconnects and PRESERVES the
in-progress segment across the connection swap; an abnormal drop (socket ends before `finished`)
reconnects; and a hard/non-recoverable error is terminal (raises, no reconnect).
"""

from __future__ import annotations

import asyncio
import json

import pytest

import app.transcribe.soniox as sx
from app.transcribe.base import FinalSegment


def tok_frame(text: str, *, lang: str = "en", s: int = 0, e: int = 100) -> str:
    return json.dumps(
        {"tokens": [{"text": text, "is_final": True, "language": lang, "start_ms": s, "end_ms": e}]}
    )


MARK = json.dumps({"tokens": [{"text": "<end>"}]})
FIN = json.dumps({"finished": True})


def err_frame(etype: str) -> str:
    return json.dumps({"error_code": 503, "error_type": etype, "error_message": "boom"})


class FakeWS:
    """A scripted stand-in for a Soniox websocket connection (async context manager + iterator)."""

    def __init__(self, frames: list[str]) -> None:
        self.frames = list(frames)
        self.sent: list = []
        self.entered = False

    async def __aenter__(self) -> FakeWS:
        self.entered = True
        return self

    async def __aexit__(self, *exc) -> bool:
        return False

    async def send(self, data) -> None:
        self.sent.append(data)

    def __aiter__(self):
        return self._iter()

    async def _iter(self):
        for f in self.frames:
            yield f
        # iterator ends here = the server closed the connection (StopAsyncIteration)


async def _infinite_audio():
    # Never exhausts during a test, so `audio_done` stays False and reconnects can happen; the
    # sender task is cancelled when the stream ends on a `finished` frame.
    while True:
        yield b"\x00" * 320
        await asyncio.sleep(0)


async def _collect(transcriber, conns, monkeypatch) -> list:
    it = iter(conns)
    monkeypatch.setattr(sx.websockets, "connect", lambda *a, **k: next(it))
    return [ev async for ev in transcriber.stream(_infinite_audio())]


def _segs(events) -> list[FinalSegment]:
    return [e for e in events if isinstance(e, FinalSegment)]


def test_clean_stream(monkeypatch):
    monkeypatch.setattr(sx, "_BACKOFF_BASE_S", 0.0)
    t = sx.SonioxTranscriber(api_key="k", language_hints=["en"])
    conns = [FakeWS([tok_frame("Hello "), MARK, FIN])]
    out = asyncio.run(_collect(t, conns, monkeypatch))
    segs = _segs(out)
    assert len(segs) == 1 and segs[0].text == "Hello"


def test_recoverable_error_reconnects_and_preserves_segment(monkeypatch):
    monkeypatch.setattr(sx, "_BACKOFF_BASE_S", 0.0)
    t = sx.SonioxTranscriber(api_key="k", language_hints=["en"])
    conns = [
        FakeWS([tok_frame("Partial "), err_frame("service_unavailable")]),  # recoverable -> retry
        FakeWS([tok_frame("world ", s=300, e=600), MARK, FIN]),
    ]
    out = asyncio.run(_collect(t, conns, monkeypatch))
    segs = _segs(out)
    # The "Partial " final tokens from connection #1 survive the swap and join "world " on #2.
    assert len(segs) == 1 and segs[0].text == "Partial world"
    assert conns[1].entered  # we actually reconnected


def test_abnormal_drop_reconnects(monkeypatch):
    monkeypatch.setattr(sx, "_BACKOFF_BASE_S", 0.0)
    t = sx.SonioxTranscriber(api_key="k", language_hints=["en"])
    conns = [
        FakeWS([tok_frame("Partial ")]),  # socket ends with no `finished` -> _Dropped -> reconnect
        FakeWS([tok_frame("world ", s=300, e=600), MARK, FIN]),
    ]
    out = asyncio.run(_collect(t, conns, monkeypatch))
    segs = _segs(out)
    assert len(segs) == 1 and segs[0].text == "Partial world"
    assert conns[1].entered


def test_nonrecoverable_error_is_terminal(monkeypatch):
    monkeypatch.setattr(sx, "_BACKOFF_BASE_S", 0.0)
    t = sx.SonioxTranscriber(api_key="k", language_hints=["en"])
    conns = [
        FakeWS([tok_frame("hi "), err_frame("insufficient_credit")]),  # NOT recoverable
        FakeWS([FIN]),  # must never be used
    ]
    with pytest.raises(sx.SonioxError):
        asyncio.run(_collect(t, conns, monkeypatch))
    assert conns[1].entered is False  # terminal error did not reconnect
