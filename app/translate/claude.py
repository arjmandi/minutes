"""Claude-backed translator (Anthropic API).

One call per segment translates into all requested targets at once, returning a JSON map. The
session custom vocabulary is passed so terminology stays consistent with the transcript. Failures
return {} (best-effort) — translation lag/errors must never block the STT path.
"""

from __future__ import annotations

import json

from anthropic import AsyncAnthropic

from app.logging import get_logger

log = get_logger("translate")


def _extract_json_object(raw: str) -> str:
    """Pull the JSON object out of a model reply, tolerating code fences and single-line output."""
    start, end = raw.find("{"), raw.rfind("}")
    return raw[start : end + 1] if start != -1 and end > start else raw.strip()


class ClaudeTranslator:
    def __init__(
        self,
        *,
        api_key: str,
        model: str = "claude-haiku-4-5-20251001",
        vocabulary: list[str] | None = None,
    ) -> None:
        self._client = AsyncAnthropic(api_key=api_key)
        self._model = model
        self._vocabulary = vocabulary or []

    async def translate(
        self,
        text: str,
        *,
        source_language: str | None,
        target_languages: list[str],
        vocabulary: list[str] | None = None,
    ) -> dict[str, str]:
        targets = [t for t in target_languages if t != source_language]
        if not targets or not text.strip():
            return {}
        terms = vocabulary if vocabulary is not None else self._vocabulary
        system = "You are a professional meeting-transcript translator. Translate faithfully and "
        system += "concisely, preserving named entities and acronyms. "
        if terms:
            system += f"Keep these domain terms exactly as given: {', '.join(terms)}. "
        system += (
            "Return ONLY a minified JSON object mapping each requested ISO language code to its "
            "translation of the text — no prose, no code fences."
        )
        user = (
            f"Source language: {source_language or 'unknown'}. "
            f"Target languages: {', '.join(targets)}.\nText: {text}"
        )
        try:
            resp = await self._client.messages.create(
                model=self._model,
                max_tokens=1024,
                system=system,
                messages=[{"role": "user", "content": user}],
            )
            data = json.loads(_extract_json_object(resp.content[0].text))
            return {t: str(data[t]) for t in targets if isinstance(data.get(t), str)}
        except Exception as exc:  # noqa: BLE001 — best-effort; never propagate into the STT path
            log.warning("translate.failed", model=self._model, error=repr(exc))
            return {}
