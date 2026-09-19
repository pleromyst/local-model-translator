import base64

import pytest
from PySide6.QtGui import QImage

from locallens.services.ocr_service import OCRService


ENGLISH_SCREENSHOT = (
    "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"
    "FCAAAAAElFTkSuQmCC"
)

CHINESE_SCREENSHOT = (
    "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"
)


@pytest.fixture(scope="module")
def ocr_service():
    return OCRService()


def screenshot(encoded: str) -> QImage:
    image = QImage.fromData(base64.b64decode(encoded), "PNG")
    assert not image.isNull()
    return image


def test_live_rapidocr_recognizes_english_screenshot(ocr_service):
    recognized = ocr_service.recognize(screenshot(ENGLISH_SCREENSHOT))

    assert recognized.lower() == "this is a test"


def test_live_rapidocr_recognizes_simplified_chinese_screenshot(ocr_service):
    recognized = ocr_service.recognize(screenshot(CHINESE_SCREENSHOT))

    assert recognized.startswith("\u8fd9\u662f")
    assert recognized.endswith("\u6d4b\u8bd5")
    assert "\n" not in recognized


def test_runtime_ocr_initialization_does_not_download_models(monkeypatch):
    import rapidocr.utils.download_file as download_file

    def reject_network(*args, **kwargs):
        raise AssertionError("OCR initialization attempted a network download")

    monkeypatch.setattr(download_file.requests, "get", reject_network)
    service = OCRService()

    assert service.recognize(screenshot(ENGLISH_SCREENSHOT)).lower() == "this is a test"
