"""Audio wire-frame encode/decode tests."""

from __future__ import annotations

import pytest

from app.audio.frames import HEADER_SIZE, decode_frame, encode_frame


def test_round_trip():
    pcm = b"\x01\x02" * 320
    frame = decode_frame(encode_frame(7, 123456, pcm))
    assert frame.seq == 7
    assert frame.ts_ms == 123456
    assert frame.pcm == pcm
    assert frame.gap is False


def test_gap_flag():
    frame = decode_frame(encode_frame(1, 0, b"", gap=True))
    assert frame.gap is True
    assert frame.pcm == b""


def test_short_frame_rejected():
    with pytest.raises(ValueError):
        decode_frame(b"\x00" * (HEADER_SIZE - 1))
