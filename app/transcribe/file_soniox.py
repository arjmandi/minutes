"""Soniox async (file) transcription client.

Flow (REST): upload the file -> create a transcription -> poll until completed -> fetch the
transcript tokens -> group into segments on endpoint markers / speaker changes. Bearer-authed with
the per-user Soniox key.

Live-validate (mirrors the RT client TODO): confirm the async model id + the file/transcription/
token JSON shapes against the live API before relying on this in production; en/de/fa coverage.
"""

from __future__ import annotations

import asyncio

import httpx

from app.logging import get_logger
from app.transcribe.file_base import FileSegment

log = get_logger("soniox_file")

_BASE = "https://api.soniox.com/v1"
_MODEL = "stt-async-v2"
_SEGMENT_MARKERS = {"<end>", "<fin>"}


class SonioxFileError(RuntimeError):
    pass


def _raise_for_status(resp: httpx.Response, stage: str) -> None:
    if resp.status_code >= 400:
        raise SonioxFileError(f"soniox {stage} failed: {resp.status_code} {resp.text[:200]}")


class SonioxFileTranscriber:
    def __init__(
        self,
        *,
        api_key: str,
        language_hints: list[str] | None = None,
        model: str = _MODEL,
        base_url: str = _BASE,
        poll_interval_s: float = 3.0,
        timeout_s: float = 1800.0,
    ) -> None:
        self._api_key = api_key
        self._language_hints = language_hints or []
        self._model = model
        self._base = base_url.rstrip("/")
        self._poll_interval_s = poll_interval_s
        self._timeout_s = timeout_s

    @property
    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._api_key}"}

    async def transcribe(
        self, audio: bytes, *, language_hints: list[str] | None = None
    ) -> list[FileSegment]:
        hints = language_hints if language_hints is not None else self._language_hints
        async with httpx.AsyncClient(timeout=httpx.Timeout(60.0)) as client:
            file_id = await self._upload(client, audio)
            try:
                tx_id = await self._create(client, file_id, hints)
                await self._await_completion(client, tx_id)
                tokens = await self._fetch_tokens(client, tx_id)
            finally:
                await self._delete_file(client, file_id)
        return _tokens_to_segments(tokens)

    async def _upload(self, client: httpx.AsyncClient, audio: bytes) -> str:
        resp = await client.post(
            f"{self._base}/files",
            headers=self._headers,
            files={"file": ("upload.audio", audio)},
        )
        _raise_for_status(resp, "upload")
        return str(resp.json()["id"])

    async def _create(
        self, client: httpx.AsyncClient, file_id: str, hints: list[str]
    ) -> str:
        body: dict = {"file_id": file_id, "model": self._model}
        if hints:
            body["language_hints"] = hints
        resp = await client.post(
            f"{self._base}/transcriptions", headers=self._headers, json=body
        )
        _raise_for_status(resp, "create")
        return str(resp.json()["id"])

    async def _await_completion(self, client: httpx.AsyncClient, tx_id: str) -> None:
        waited = 0.0
        while waited < self._timeout_s:
            resp = await client.get(
                f"{self._base}/transcriptions/{tx_id}", headers=self._headers
            )
            _raise_for_status(resp, "status")
            data = resp.json()
            status = data.get("status")
            if status == "completed":
                return
            if status == "error":
                raise SonioxFileError(data.get("error_message") or "transcription error")
            await asyncio.sleep(self._poll_interval_s)
            waited += self._poll_interval_s
        raise SonioxFileError("transcription timed out")

    async def _fetch_tokens(self, client: httpx.AsyncClient, tx_id: str) -> list[dict]:
        resp = await client.get(
            f"{self._base}/transcriptions/{tx_id}/transcript", headers=self._headers
        )
        _raise_for_status(resp, "transcript")
        return list(resp.json().get("tokens") or [])

    async def _delete_file(self, client: httpx.AsyncClient, file_id: str) -> None:
        try:
            await client.delete(f"{self._base}/files/{file_id}", headers=self._headers)
        except Exception as exc:  # noqa: BLE001 — best-effort cleanup; never fail the job on this
            log.warning("soniox_file.cleanup_failed", file_id=file_id, error=repr(exc))


def _tokens_to_segments(tokens: list[dict]) -> list[FileSegment]:
    """Group Soniox tokens into segments, breaking on endpoint markers / speaker changes."""
    segments: list[FileSegment] = []
    buf: list[str] = []
    start_ms: int | None = None
    end_ms: int | None = None
    speaker = "mixed"
    language: str | None = None

    def flush() -> None:
        nonlocal buf, start_ms, end_ms, speaker, language
        text = "".join(buf).strip()
        if text:
            segments.append(
                FileSegment(
                    text=text,
                    start_ms=start_ms,
                    end_ms=end_ms,
                    language=language,
                    speaker_id=speaker,
                )
            )
        buf, start_ms, end_ms = [], None, None

    for tok in tokens:
        text = str(tok.get("text", ""))
        tok_speaker = str(tok.get("speaker") or "mixed")
        if buf and tok_speaker != speaker:
            flush()
        speaker = tok_speaker
        if text in _SEGMENT_MARKERS:
            flush()
            continue
        if not buf:
            start_ms = tok.get("start_ms")
            language = tok.get("language")
        buf.append(text)
        end_ms = tok.get("end_ms", end_ms)
    flush()
    return segments
