"""Wire-frame protocol between the capture client and the ingest WebSocket.

Binary audio frames carry a fixed 13-byte little-endian header followed by raw PCM
(16 kHz mono s16le, to feed Soniox directly):

    seq    uint32   monotonic per connection
    ts_ms  uint64   timestamp from the client's cumulative sample count (not wall clock)
    flags  uint8    bit0 = gap-before-this-frame (capture discontinuity)

Control messages (hello / end / heartbeat) are JSON text frames, handled in the ingest endpoint.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

_HEADER = struct.Struct("<IQB")
HEADER_SIZE = _HEADER.size  # 13
_FLAG_GAP = 0x01


@dataclass(slots=True)
class PcmFrame:
    seq: int
    ts_ms: int
    pcm: bytes
    gap: bool = False


def encode_frame(seq: int, ts_ms: int, pcm: bytes, *, gap: bool = False) -> bytes:
    header = _HEADER.pack(
        seq & 0xFFFFFFFF, ts_ms & 0xFFFFFFFFFFFFFFFF, _FLAG_GAP if gap else 0
    )
    return header + pcm


def decode_frame(data: bytes) -> PcmFrame:
    if len(data) < HEADER_SIZE:
        raise ValueError("audio frame shorter than header")
    seq, ts_ms, flags = _HEADER.unpack_from(data, 0)
    return PcmFrame(seq=seq, ts_ms=ts_ms, pcm=data[HEADER_SIZE:], gap=bool(flags & _FLAG_GAP))
