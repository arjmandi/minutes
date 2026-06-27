"""In-memory storage for tests / key-less local runs (records uploads, no network)."""

from __future__ import annotations


class FakeStorage:
    def __init__(self) -> None:
        self.uploads: dict[str, int] = {}  # key -> byte length

    async def upload(self, key: str, data: bytes, *, content_type: str = "audio/wav") -> None:
        self.uploads[key] = len(data)

    async def delete(self, key: str) -> None:
        self.uploads.pop(key, None)
