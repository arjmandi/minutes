"""Object-storage abstraction (S3 protocol). FakeStorage for tests; SpacesStorage for S3-compatible object storage."""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Storage(Protocol):
    async def upload(self, key: str, data: bytes, *, content_type: str = "audio/wav") -> None: ...

    async def delete(self, key: str) -> None: ...

    async def head(self, key: str) -> bool:
        """True if the object exists (used by the orphan reconciler)."""
        ...
