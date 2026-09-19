import os
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QEvent, QPoint, QPointF, QRect, QSize, Qt
from PySide6.QtGui import QMouseEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from locallens.ui.popup import TranslationPopup


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
    raise AssertionError("timed out while waiting for popup state")


def test_popup_has_required_window_flags_and_translucent_shell(application):
    popup = TranslationPopup()
    flags = popup.windowFlags()

    assert flags & Qt.WindowType.FramelessWindowHint
    assert flags & Qt.WindowType.WindowStaysOnTopHint
    assert flags & Qt.WindowType.Tool
    assert not flags & Qt.WindowType.WindowTransparentForInput
    assert popup.testAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    assert popup.testAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
    assert popup.minimumWidth() == 280
    assert popup.maximumWidth() == 520
    popup.close()


def test_popup_requests_native_acrylic_after_its_window_exists(application):
    window_ids = []

    def enable(window_id):
        window_ids.append(window_id)
        return True

    popup = TranslationPopup(acrylic_enabler=enable)
    popup.show_translating(
        anchor=QPoint(100, 100), available_geometry=QRect(0, 0, 800, 600)
    )
    application.processEvents()

    assert window_ids == [int(popup.winId())]
    assert popup.acrylic_enabled is True
    assert "rgba(24, 20, 34, 82)" in popup.styleSheet()
    popup.close()


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [
        (QPoint(400, 300), QPoint(416, 316)),
        (QPoint(1080, 730), QPoint(644, 474)),
        (QPoint(105, 55), QPoint(121, 71)),
    ],
)
def test_position_flips_and_stays_inside_screen(anchor, expected):
    available = QRect(100, 50, 1000, 700)

    position = TranslationPopup.calculate_position(
        anchor, QSize(420, 240), available
    )

    assert position == expected
    assert position.x() >= available.left()
    assert position.y() >= available.top()
    assert position.x() + 420 <= available.right() + 1
    assert position.y() + 240 <= available.bottom() + 1


@pytest.mark.parametrize(
    "anchor",
    [
        QPoint(-1915, -195),
        QPoint(-960, 300),
        QPoint(-5, 875),
    ],
)
def test_position_stays_inside_display_with_negative_desktop_coordinates(anchor):
    available = QRect(-1920, -200, 1920, 1080)

    position = TranslationPopup.calculate_position(
        anchor, QSize(420, 240), available
    )

    assert position.x() >= available.left()
    assert position.y() >= available.top()
    assert position.x() + 420 <= available.right() + 1
    assert position.y() + 240 <= available.bottom() + 1


def test_manual_position_is_clamped_on_negative_coordinate_display():
    available = QRect(-1920, -200, 1920, 1080)

    assert TranslationPopup.clamp_position(
        QPoint(-2500, -500), QSize(420, 240), available
    ) == QPoint(-1920, -200)
    assert TranslationPopup.clamp_position(
        QPoint(100, 1000), QSize(420, 240), available
    ) == QPoint(-420, 640)


def test_translating_and_result_reuse_same_popup_window(application):
    popup = TranslationPopup()
    geometry = QRect(0, 0, 1920, 1080)
    popup.show_reading_selection(anchor=QPoint(900, 500), available_geometry=geometry)
    wait_until(application, popup.isVisible)
    window_id = int(popup.winId())

    assert popup.body_edit.toPlainText() == "Reading selected text..."
    assert not popup.copy_button.isEnabled()

    popup.show_recognizing(anchor=QPoint(900, 500), available_geometry=geometry)
    wait_until(application, popup.isVisible)

    assert int(popup.winId()) == window_id
    assert popup.body_edit.toPlainText() == "Recognizing text..."
    assert not popup.copy_button.isEnabled()

    popup.show_translating()
    application.processEvents()

    assert int(popup.winId()) == window_id
    assert popup.body_edit.toPlainText() == "Translating..."
    assert not popup.copy_button.isEnabled()

    popup.show_translation("这是一个测试。")
    application.processEvents()

    assert int(popup.winId()) == window_id
    assert popup.body_edit.toPlainText() == "这是一个测试。"
    assert popup.copy_button.isEnabled()
    assert popup.height() <= round(geometry.height() * 0.45)
    popup.close()


def test_copy_feedback_uses_injected_clipboard_writer(application):
    copied = []
    popup = TranslationPopup(copy_text=copied.append)
    popup.show_translating(anchor=QPoint(200, 200), available_geometry=QRect(0, 0, 1280, 720))
    popup.show_translation("要复制的译文")
    popup.copy_button.click()
    application.processEvents()

    assert copied == ["要复制的译文"]
    assert popup.copy_button.text() == "Copied"
    popup.close()


def test_escape_closes_popup_while_body_has_focus(application):
    popup = TranslationPopup()
    popup.show_translating(anchor=QPoint(200, 200), available_geometry=QRect(0, 0, 1280, 720))
    popup.show_translation("Selectable text")
    popup.body_edit.setFocus()
    application.processEvents()

    QTest.keyClick(popup.body_edit, Qt.Key.Key_Escape)
    wait_until(application, lambda: not popup.isVisible())

    assert popup.result_text == ""


def test_long_text_wraps_to_popup_width_without_horizontal_scrollbar(application):
    popup = TranslationPopup()
    popup.show_translating(anchor=QPoint(200, 200), available_geometry=QRect(0, 0, 1280, 720))
    popup.show_translation(
        "一段很长的中文内容" * 30
        + " DEFAULT_SETTINGSusageStatsWithoutAnyNaturalBreakPoint" * 8
    )
    application.processEvents()

    assert popup.body_edit.lineWrapMode() == popup.body_edit.LineWrapMode.WidgetWidth
    assert popup.body_edit.wordWrapMode() == (
        popup.body_edit.wordWrapMode().WrapAtWordBoundaryOrAnywhere
    )
    assert popup.body_edit.horizontalScrollBarPolicy() == (
        Qt.ScrollBarPolicy.ScrollBarAlwaysOff
    )
    assert popup.body_edit.document().textWidth() <= popup.body_edit.viewport().width()
    popup.close()


def test_title_area_drags_popup_and_keeps_manual_position(application):
    popup = TranslationPopup()
    geometry = QRect(0, 0, 1280, 720)
    popup.show_translating(anchor=QPoint(200, 200), available_geometry=geometry)
    popup.show_translation("Translation")
    application.processEvents()

    press_global = popup.frameGeometry().topLeft() + QPoint(70, 32)
    press_local = popup.title_label.mapFromGlobal(press_global)
    press = QMouseEvent(
        QEvent.Type.MouseButtonPress,
        QPointF(press_local),
        QPointF(press_global),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(popup.title_label, press)

    move_global = press_global + QPoint(90, 45)
    move_local = popup.title_label.mapFromGlobal(move_global)
    move = QMouseEvent(
        QEvent.Type.MouseMove,
        QPointF(move_local),
        QPointF(move_global),
        Qt.MouseButton.NoButton,
        Qt.MouseButton.LeftButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(popup.title_label, move)
    dragged_position = popup.pos()

    release = QMouseEvent(
        QEvent.Type.MouseButtonRelease,
        QPointF(popup.title_label.mapFromGlobal(move_global)),
        QPointF(move_global),
        Qt.MouseButton.LeftButton,
        Qt.MouseButton.NoButton,
        Qt.KeyboardModifier.NoModifier,
    )
    QApplication.sendEvent(popup.title_label, release)

    assert dragged_position == QPoint(306, 261)
    assert popup._manually_positioned is True

    popup.show_translation("Updated translation")
    application.processEvents()

    assert popup.pos() == dragged_position
    popup.close()
