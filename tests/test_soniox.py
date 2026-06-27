"""SonioxTranscriber protocol tests against a scripted fake WS server (offline, no key).

Exercises the wire-protocol parsing the FakeTranscriber can't: interim hypotheses, is_final +
<end> segmentation, per-token language/timing, the finished terminal frame, and the recoverable
503 error path.
"""

from __future__ import annotations

import asyncio
import json

import pytest
import websockets

from app.transcribe.base import FinalSegment, Interim
from app.transcribe.soniox import SonioxError, SonioxTranscriber


async def _fake_server(scripts: list[dict]):
    async def handler(ws):
        await ws.recv()  # the JSON config (first frame)
        for message in scripts:
            await ws.send(json.dumps(message))
        # handler returns -> server closes the connection

    server = await websockets.serve(handler, "localhost", 0)
    port = server.sockets[0].getsockname()[1]
    return server, port


async def _collect(url: str) -> list:
    async def audio():
        yield b"\x00\x00" * 160  # one tiny PCM chunk, then the iterator ends

    transcriber = SonioxTranscriber(api_key="test", language_hints=["en", "de", "fa"], url=url)
    events = []
    async for event in transcriber.stream(audio()):
        events.append(event)
    return events


async def test_interim_and_final_segmentation():
    scripts = [
        {"tokens": [{"text": "hello ", "is_final": False, "language": "en"}]},
        {
            "tokens": [
                {"text": "hello world", "is_final": True, "language": "en",
                 "start_ms": 0, "end_ms": 500},
                {"text": "<end>", "is_final": True},
            ]
        },
        {"tokens": [], "finished": True},
    ]
    server, port = await _fake_server(scripts)
    try:
        events = await asyncio.wait_for(_collect(f"ws://localhost:{port}"), timeout=10)
    finally:
        server.close()
        await server.wait_closed()

    interims = [e for e in events if isinstance(e, Interim)]
    finals = [e for e in events if isinstance(e, FinalSegment)]
    assert any(e.text == "hello" for e in interims)  # full hypothesis, stripped
    assert len(finals) == 1
    seg = finals[0]
    assert seg.text == "hello world"
    assert seg.language == "en"
    assert seg.start_ms == 0 and seg.end_ms == 500
    assert seg.utterance_id == "u1"
    assert "<end>" not in seg.text  # marker filtered out


async def test_multiple_segments_get_distinct_ids():
    scripts = [
        {"tokens": [
            {"text": "one", "is_final": True, "language": "en"},
            {"text": "<end>", "is_final": True},
        ]},
        {"tokens": [
            {"text": "zwei", "is_final": True, "language": "de"},
            {"text": "<end>", "is_final": True},
        ]},
        {"tokens": [], "finished": True},
    ]
    server, port = await _fake_server(scripts)
    try:
        events = await asyncio.wait_for(_collect(f"ws://localhost:{port}"), timeout=10)
    finally:
        server.close()
        await server.wait_closed()
    finals = [e for e in events if isinstance(e, FinalSegment)]
    assert [s.text for s in finals] == ["one", "zwei"]
    assert [s.utterance_id for s in finals] == ["u1", "u2"]
    assert [s.language for s in finals] == ["en", "de"]


async def test_503_is_recoverable():
    scripts = [
        {"tokens": [], "error_code": 503, "error_type": "service_unavailable",
         "error_message": "max connection duration"}
    ]
    server, port = await _fake_server(scripts)
    try:
        with pytest.raises(SonioxError) as excinfo:
            await asyncio.wait_for(_collect(f"ws://localhost:{port}"), timeout=10)
        assert excinfo.value.recoverable is True
    finally:
        server.close()
        await server.wait_closed()
