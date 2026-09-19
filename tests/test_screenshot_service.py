import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QRect, QSize
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from locallens.services.screenshot_service import (
    ScreenCapture,
    ScreenshotError,
    ScreenshotService,
)


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def make_high_dpi_capture():
    pixmap = QPixmap(400, 200)
    pixmap.fill("#336699")
    pixmap.setDevicePixelRatio(2.0)
    return ScreenCapture(
        "secondary",
        QRect(-200, -100, 200, 100),
        pixmap,
    )


def test_crop_converts_logical_selection_to_physical_pixels(application):
    capture = make_high_dpi_capture()

    image = capture.crop(QRect(10, 15, 50, 20))

    assert capture.scale_x == 2.0
    assert capture.scale_y == 2.0
    assert image.size().width() == 100
    assert image.size().height() == 40
    assert image.devicePixelRatio() == 1.0


def test_negative_global_geometry_converts_to_local_selection(application):
    capture = make_high_dpi_capture()

    local = capture.global_to_local(QRect(-190, -85, 50, 20))

    assert local == QRect(10, 15, 50, 20)
    assert capture.crop(local).size().width() == 100


def test_crop_handles_fractional_display_scale_without_losing_edge_pixels(
    application,
):
    pixmap = QPixmap(125, 75)
    pixmap.fill("blue")
    capture = ScreenCapture("fractional-scale", QRect(-100, 50, 100, 60), pixmap)

    image = capture.crop(QRect(3, 4, 10, 8))

    assert capture.scale_x == 1.25
    assert capture.scale_y == 1.25
    assert image.size() == QSize(14, 10)
    assert image.devicePixelRatio() == 1.0


def test_crop_clamps_edges_and_rejects_empty_area(application):
    capture = make_high_dpi_capture()

    clamped = capture.crop(QRect(-10, -10, 20, 20))
    empty = capture.crop(QRect(40, 30, 1, 20))

    assert clamped.size().width() == 20
    assert clamped.size().height() == 20
    assert empty.isNull()


class FakeScreen:
    def __init__(self, pixmap):
        self._pixmap = pixmap

    def name(self):
        return "fake-screen"

    def geometry(self):
        return QRect(-1280, 0, 1280, 720)

    def grabWindow(self, window_id):  # noqa: N802
        assert window_id == 0
        return self._pixmap


def test_capture_screen_keeps_display_geometry(application):
    pixmap = QPixmap(2560, 1440)
    pixmap.fill("black")

    capture = ScreenshotService.capture_screen(FakeScreen(pixmap))  # type: ignore[arg-type]

    assert capture.screen_name == "fake-screen"
    assert capture.screen_geometry == QRect(-1280, 0, 1280, 720)
    assert capture.scale_x == 2.0
    assert capture.scale_y == 2.0


def test_null_screen_capture_is_reported(application):
    with pytest.raises(ScreenshotError, match="could not be captured"):
        ScreenshotService.capture_screen(FakeScreen(QPixmap()))  # type: ignore[arg-type]


def test_debug_capture_is_saved_only_when_explicitly_requested(application, tmp_path):
    image = QImage(40, 20, QImage.Format.Format_ARGB32)
    image.fill("red")
    output = tmp_path / "debug_capture.png"

    saved_path = ScreenshotService.save_debug_capture(image, output)

    assert saved_path == output
    assert output.is_file()


def test_debug_capture_cleanup_removes_only_the_requested_artifact(tmp_path):
    debug_capture = tmp_path / "debug_capture.png"
    unrelated = tmp_path / "keep.txt"
    debug_capture.write_bytes(b"debug image")
    unrelated.write_text("keep", encoding="utf-8")

    assert ScreenshotService.remove_debug_capture(debug_capture) is True
    assert not debug_capture.exists()
    assert unrelated.read_text(encoding="utf-8") == "keep"
    assert ScreenshotService.remove_debug_capture(debug_capture) is True
