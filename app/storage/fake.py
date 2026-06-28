"""In-memory storage for tests / key-less local runs (records uploads, no network)."""

from __future__ import annotations


class FakeStorage:
    # Process-shared "bucket" so separate instances (e.g. the API process and the upload worker,
    # each of which builds its own FakeStorage) see the same objects — like a real shared bucket.
    _BUCKET: dict[str, bytes] = {}

    def __init__(self) -> None:
        self.uploads: dict[str, bytes] = FakeStorage._BUCKET  # key -> object bytes

    async def upload(self, key: str, data: bytes, *, content_type: str = "audio/wav") -> None:
        self.uploads[key] = data

    async def download(self, key: str) -> bytes:
        return self.uploads[key]

    async def delete(self, key: str) -> None:
        self.uploads.pop(key, None)

    async def head(self, key: str) -> bool:
        return key in self.uploads
