import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QPoint, QRect, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from locallens.services.screenshot_service import ScreenCapture
from locallens.ui.capture_overlay import CaptureOverlay


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def wait_until(application, predicate, timeout=1.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        application.processEvents()
        if predicate():
            return
        time.sleep(0.002)
    raise AssertionError("timed out while waiting for capture overlay")


def make_capture(geometry=QRect(0, 0, 200, 100)):
    pixmap = QPixmap(geometry.width() * 2, geometry.height() * 2)
    pixmap.fill("#557799")
    pixmap.setDevicePixelRatio(2.0)
    return ScreenCapture("test-screen", geometry, pixmap)


def test_overlay_uses_full_display_geometry_and_required_flags(application):
    geometry = QRect(-1600, -200, 200, 100)
    overlay = CaptureOverlay(make_capture(geometry))
    flags = overlay.windowFlags()

    assert overlay.geometry() == geometry
    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.Tool
    overlay._completed = True
    overlay.close()


def test_drag_returns_high_resolution_selected_image(application):
    overlay = CaptureOverlay(make_capture())
    captured = []
    cancelled = []
    overlay.captured.connect(captured.append)
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay.start()
    application.processEvents()

    QTest.mousePress(
        overlay, Qt.MouseButton.LeftButton, pos=QPoint(10, 10)
    )
    QTest.mouseMove(overlay, QPoint(60, 40))
    QTest.mouseRelease(
        overlay, Qt.MouseButton.LeftButton, pos=QPoint(60, 40)
    )
    wait_until(application, lambda: bool(captured))

    assert captured[0].width() == 102
    assert captured[0].height() == 62
    assert cancelled == []
    assert not overlay.isVisible()


def test_empty_selection_stays_open_and_escape_cancels(application):
    overlay = CaptureOverlay(make_capture())
    empty_selections = []
    cancelled = []
    overlay.empty_selection.connect(lambda: empty_selections.append(True))
    overlay.cancelled.connect(lambda: cancelled.append(True))
    overlay.start()
    application.processEvents()

    QTest.mousePress(
        overlay, Qt.MouseButton.LeftButton, pos=QPoint(30, 30)
    )
    QTest.mouseRelease(
        overlay, Qt.MouseButton.LeftButton, pos=QPoint(30, 30)
    )
    application.processEvents()

    assert empty_selections == [True]
    assert overlay.isVisible()
    assert overlay.selection.isEmpty()

    QTest.keyClick(overlay, Qt.Key.Key_Escape)
    wait_until(application, lambda: not overlay.isVisible())
    assert cancelled == [True]
