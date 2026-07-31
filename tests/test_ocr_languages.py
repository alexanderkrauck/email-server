"""OCR must read the languages the mail is actually written in."""

from unittest.mock import MagicMock, patch

import pytest

from src.config import settings
from src.email.text_extractor import TextExtractor


@pytest.fixture(autouse=True)
def clear_language_cache():
    TextExtractor._ocr_languages = None
    yield
    TextExtractor._ocr_languages = None


def test_every_installed_language_is_used_when_nothing_is_configured(monkeypatch):
    monkeypatch.setattr(settings, "ocr_languages", "")
    fake = MagicMock()
    fake.get_languages.return_value = ["deu", "eng", "fra", "osd"]

    with patch.dict("sys.modules", {"pytesseract": fake}):
        assert TextExtractor._resolve_ocr_languages() == "deu+eng+fra"


def test_an_explicit_setting_wins(monkeypatch):
    monkeypatch.setattr(settings, "ocr_languages", "deu+eng")
    fake = MagicMock()
    fake.get_languages.return_value = ["deu", "eng", "fra"]

    with patch.dict("sys.modules", {"pytesseract": fake}):
        assert TextExtractor._resolve_ocr_languages() == "deu+eng"


def test_enumeration_failure_falls_back_to_english(monkeypatch):
    monkeypatch.setattr(settings, "ocr_languages", "")
    fake = MagicMock()
    fake.get_languages.side_effect = RuntimeError("tesseract missing")

    with patch.dict("sys.modules", {"pytesseract": fake}):
        assert TextExtractor._resolve_ocr_languages() == "eng"


def test_the_resolved_languages_are_passed_to_tesseract(monkeypatch):
    """Without lang=, tesseract assumes English and mangles accented scripts."""
    monkeypatch.setattr(settings, "ocr_languages", "deu+eng")
    fake = MagicMock()
    fake.image_to_string.return_value = "Fußboden dürfen"
    image_module = MagicMock()

    with patch.dict("sys.modules", {"pytesseract": fake, "PIL": MagicMock(), "PIL.Image": image_module}):
        text = TextExtractor()._extract_image_ocr(b"not-a-real-image", "image/jpeg")

    assert text == "Fußboden dürfen"
    assert fake.image_to_string.call_args.kwargs["lang"] == "deu+eng"
