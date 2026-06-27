"""Translator tests (FakeTranslator). The Claude path is exercised via live validation."""

from __future__ import annotations

from app.translate.fake import FakeTranslator


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
