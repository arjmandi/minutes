"""Translator tests (FakeTranslator). The Claude path is exercised via live validation."""

from __future__ import annotations

from app.translate.claude import _extract_json_object
from app.translate.fake import FakeTranslator


def test_extract_json_object_tolerates_fences_and_single_line():
    assert _extract_json_object('```json\n{"en":"hi"}\n```') == '{"en":"hi"}'
    assert _extract_json_object('```{"en":"hi"}```') == '{"en":"hi"}'  # single-line fenced
    assert _extract_json_object('{"en":"hi"}') == '{"en":"hi"}'
    assert _extract_json_object('here you go: {"en":"hi"} done') == '{"en":"hi"}'


async def test_translates_to_targets():
    out = await FakeTranslator().translate(
        "hello", source_language="en", target_languages=["de", "fr"]
    )
    assert out == {"de": "[de] hello", "fr": "[fr] hello"}


async def test_skips_source_language():
    out = await FakeTranslator().translate(
        "hi", source_language="en", target_languages=["en", "de"]
    )
    assert "en" not in out
    assert out["de"] == "[de] hi"


async def test_empty_text_yields_nothing():
    out = await FakeTranslator().translate("   ", source_language="en", target_languages=["de"])
    assert out == {}
