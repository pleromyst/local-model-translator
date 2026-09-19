import os
import logging
import time
from types import SimpleNamespace

import numpy as np
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from locallens.services.ocr_service import (
    OCRController,
    OCRInitializationError,
    OCRRecognitionError,
    OCRService,
    qimage_to_bgr,
)
from locallens.logging_config import configure_logging, shutdown_logging


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def wait_until(application, predicate, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("timed out while waiting for OCR")


def make_image(width=120, height=60, color=QColor(10, 20, 30)):
    image = QImage(width, height, QImage.Format.Format_ARGB32)
    image.fill(color)
    return image


def test_qimage_conversion_produces_contiguous_bgr_pixels(application):
    array = qimage_to_bgr(make_image(width=3, height=2))

    assert array.shape == (2, 3, 3)
    assert array.dtype == np.uint8
    assert array.flags.c_contiguous
    assert array[0, 0].tolist() == [30, 20, 10]


def test_engine_is_initialized_once_and_reused(application):
    factory_calls = []
    engine_calls = []

    class FakeEngine:
        def __call__(self, image):
            engine_calls.append(image.shape)
            return SimpleNamespace(txts=("Hello", "世界"))

    def factory():
        factory_calls.append(True)
        return FakeEngine()

    service = OCRService(engine_factory=factory)

    assert service.recognize(make_image()) == "Hello世界"
    assert service.recognize(make_image()) == "Hello世界"
    assert len(factory_calls) == 1
    assert len(engine_calls) == 2


@pytest.mark.parametrize(
    ("texts", "boxes", "expected"),
    [
        (
            ("this", "is", "a", "test"),
            (
                ((8, 7), (73, 7), (73, 46), (8, 45)),
                ((70, 17), (103, 17), (103, 42), (70, 42)),
                ((98, 18), (121, 18), (121, 41), (98, 41)),
                ((120, 11), (184, 10), (184, 45), (120, 45)),
            ),
            "this is a test",
        ),
        (
            ("\u8fd9\u662f", "\u4e00\u4e2a\u6d4b\u8bd5"),
            (
                ((11, 2), (90, 5), (88, 50), (9, 49)),
                ((102, 1), (205, 1), (205, 50), (102, 50)),
            ),
            "\u8fd9\u662f\u4e00\u4e2a\u6d4b\u8bd5",
        ),
        (
            ("First", "line.", "Second", "line!"),
            (
                ((0, 0), (50, 0), (50, 20), (0, 20)),
                ((55, 0), (95, 0), (95, 20), (55, 20)),
                ((0, 30), (60, 30), (60, 50), (0, 50)),
                ((65, 30), (100, 30), (100, 50), (65, 50)),
            ),
            "First line. Second line!",
        ),
        (
            ("山有", "木兮；", "木有", "枝，心悦君兮，", "君", "不知。"),
            (
                ((0, 0), (50, 0), (50, 20), (0, 20)),
                ((0, 25), (50, 25), (50, 45), (0, 45)),
                ((0, 50), (50, 50), (50, 70), (0, 70)),
                ((0, 75), (130, 75), (130, 95), (0, 95)),
                ((0, 100), (25, 100), (25, 120), (0, 120)),
                ((0, 125), (50, 125), (50, 145), (0, 145)),
            ),
            "山有木兮；木有枝，心悦君兮，君不知。",
        ),
    ],
)
def test_text_boxes_are_reconstructed_into_natural_lines(
    application, texts, boxes, expected
):
    service = OCRService(
        engine_factory=lambda: lambda image: SimpleNamespace(
            txts=texts,
            boxes=boxes,
        )
    )

    assert service.recognize(make_image()) == expected


def test_low_confidence_regions_are_removed_before_text_reconstruction(application):
    boxes = (
        ((0, 0), (40, 0), (40, 20), (0, 20)),
        ((45, 0), (90, 0), (90, 20), (45, 20)),
        ((0, 30), (35, 30), (35, 50), (0, 50)),
    )
    service = OCRService(
        engine_factory=lambda: lambda image: SimpleNamespace(
            txts=("Reliable", "texture-noise", "weak"),
            boxes=boxes,
            scores=(0.98, 0.42, 0.64),
        )
    )

    assert service.recognize(make_image()) == "Reliable"


def test_all_low_confidence_regions_produce_no_text(application):
    service = OCRService(
        engine_factory=lambda: lambda image: SimpleNamespace(
            txts=("false-positive",),
            boxes=(((0, 0), (60, 0), (60, 20), (0, 20)),),
            scores=(0.4,),
        )
    )

    assert service.recognize(make_image()) == ""


def test_empty_output_and_failures_are_safe(application):
    empty_service = OCRService(
        engine_factory=lambda: lambda image: SimpleNamespace(txts=None)
    )
    assert empty_service.recognize(make_image()) == ""

    with pytest.raises(OCRRecognitionError, match="empty"):
        empty_service.recognize(QImage())

    with pytest.raises(OCRInitializationError, match="could not be initialized"):
        OCRService(engine_factory=lambda: (_ for _ in ()).throw(RuntimeError("private")))

    failing_service = OCRService(
        engine_factory=lambda: lambda image: (_ for _ in ()).throw(
            RuntimeError("private")
        )
    )
    with pytest.raises(OCRRecognitionError, match="failed unexpectedly") as error:
        failing_service.recognize(make_image())
    assert "private" not in str(error.value)


@pytest.mark.parametrize("color", [QColor(0, 0, 0), QColor(255, 255, 255)])
def test_unavailable_capture_is_rejected_before_ocr_engine(application, color):
    engine_calls = []
    service = OCRService(
        engine_factory=lambda: lambda image: engine_calls.append(image)
    )

    with pytest.raises(OCRRecognitionError, match="capture was unavailable"):
        service.recognize(make_image(color=color))

    assert engine_calls == []


def test_controller_initializes_and_recognizes_without_blocking_ui(application):
    factory_calls = []
    engine_calls = []

    class SlowEngine:
        def __call__(self, image):
            time.sleep(0.04)
            engine_calls.append(image.shape)
            return SimpleNamespace(txts=(f"result-{len(engine_calls)}",))

    def service_factory():
        factory_calls.append(True)
        return OCRService(engine_factory=SlowEngine)

    controller = OCRController(service_factory=service_factory)
    results = []
    heartbeat = []
    controller.text_ready.connect(
        lambda request_id, text: results.append((request_id, text))
    )
    wait_until(application, lambda: len(factory_calls) == 1)

    started_at = time.monotonic()
    first_id = controller.recognize(make_image())
    call_duration = time.monotonic() - started_at
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    wait_until(application, lambda: len(results) == 1)
    second_id = controller.recognize(make_image())
    wait_until(application, lambda: len(results) == 2)

    assert call_duration < 0.02
    assert heartbeat == [True]
    assert results == [(first_id, "result-1"), (second_id, "result-2")]
    assert len(factory_calls) == 1
    assert controller.shutdown()


def test_controller_emits_no_text_for_empty_result(application):
    controller = OCRController(
        service_factory=lambda: OCRService(
            engine_factory=lambda: lambda image: SimpleNamespace(txts=())
        )
    )
    empty_requests = []
    controller.no_text.connect(empty_requests.append)

    request_id = controller.recognize(make_image())
    wait_until(application, lambda: bool(empty_requests))

    assert empty_requests == [request_id]
    assert controller.shutdown()


def test_ocr_log_records_duration_without_recognized_text(application, tmp_path):
    log_path = tmp_path / "locallens.log"
    configure_logging(path=log_path)
    private_text = "PRIVATE_OCR_TEXT_DO_NOT_LOG"
    service = OCRService(
        engine_factory=lambda: lambda image: SimpleNamespace(txts=(private_text,))
    )

    assert service.recognize(make_image()) == private_text
    for handler in logging.getLogger("locallens").handlers:
        handler.flush()
    content = log_path.read_text(encoding="utf-8")

    assert "ocr_completed status=success" in content
    assert "duration_ms=" in content
    assert private_text not in content
    shutdown_logging()
