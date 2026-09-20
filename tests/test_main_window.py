import os
import threading
import time
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, QRect, QTimer, Signal
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication

from locallens.config import DEFAULT_SETTINGS
from locallens.hotkeys import HotkeySpec, MOD_ALT, MOD_NOREPEAT, VK_MENU
from locallens.services.ollama_client import OllamaConnectionError
from locallens.services.ocr_service import INVALID_CAPTURE_MESSAGE
from locallens.services.screenshot_service import ScreenCapture
from locallens.ui.main_window import MainWindow


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
        time.sleep(0.005)
    raise AssertionError("timed out while waiting for the Qt condition")


def test_main_window_uses_release_title(application):
    window = MainWindow(DEFAULT_SETTINGS)

    assert window.windowTitle() == "LocalLens"
    window.close()


class FakeClient:
    def __init__(self, result="译文", error=None, delay=0.02):
        self.result = result
        self.error = error
        self.delay = delay
        self.calls = []
        self.closed = False

    def translate(self, text):
        self.calls.append(text)
        time.sleep(self.delay)
        if self.error is not None:
            raise self.error
        return self.result

    def close(self):
        self.closed = True


class OutOfOrderClient:
    def __init__(self):
        self.started = {"A": threading.Event(), "B": threading.Event()}
        self.release = {"A": threading.Event(), "B": threading.Event()}

    def translate(self, text):
        self.started[text].set()
        if not self.release[text].wait(timeout=1.5):
            raise RuntimeError(f"request {text} was not released")
        return f"result-{text}"

    def close(self):
        pass


class FakeSelectionService:
    def __init__(self):
        self.release_keys = None

    def capture_selected_text(self, release_keys):
        self.release_keys = release_keys
        return True


class FakeScreenshotService:
    def __init__(self):
        pixmap = QPixmap(400, 200)
        pixmap.fill("#223344")
        self.capture = ScreenCapture("fake", QRect(0, 0, 200, 100), pixmap)
        self.capture_calls = 0
        self.saved_images = []
        self.remove_debug_capture_calls = 0

    def capture_current_screen(self, position=None):
        self.capture_calls += 1
        return self.capture

    def save_debug_capture(self, image):
        self.saved_images.append(image.copy())

    def remove_debug_capture(self):
        self.remove_debug_capture_calls += 1
        return True


class FakeOCRController(QObject):
    text_ready = Signal(int, str)
    no_text = Signal(int)
    failed = Signal(int, str)

    def __init__(self):
        super().__init__()
        self.images = []
        self.shutdown_calls = 0
        self.invalidate_calls = 0

    def recognize(self, image):
        self.images.append(image.copy())
        return len(self.images)

    def shutdown(self, timeout_ms=5000):
        self.shutdown_calls += 1
        return True

    def invalidate_active_request(self):
        self.invalidate_calls += 1


def test_hotkey_waits_for_trigger_key_as_well_as_modifiers(application):
    window = MainWindow(DEFAULT_SETTINGS)
    selection_service = FakeSelectionService()
    window._selection_service = selection_service
    spec = HotkeySpec(
        display_name="Alt+T",
        modifiers=MOD_ALT | MOD_NOREPEAT,
        virtual_key=ord("T"),
        modifier_virtual_keys=(VK_MENU,),
    )

    window._on_translation_hotkey(spec)

    assert selection_service.release_keys == (VK_MENU, ord("T"))
    window._selection_service = None
    window.close()


def test_selection_started_immediately_shows_reading_popup(application):
    window = MainWindow(DEFAULT_SETTINGS)
    window._translation_hotkey_started_at = time.monotonic()

    window._on_selection_started()
    application.processEvents()

    assert window.popup.isVisible()
    assert window.popup.body_edit.toPlainText() == "Reading selected text..."
    assert window._translation_hotkey_started_at is None
    window.popup.close()
    window.close()


def test_global_hotkeys_dispatch_translation_and_area_capture(application):
    screenshot_service = FakeScreenshotService()
    window = MainWindow(DEFAULT_SETTINGS, screenshot_service=screenshot_service)
    selection_service = FakeSelectionService()
    window._selection_service = selection_service
    translation_spec = HotkeySpec(
        display_name="Alt+T",
        modifiers=MOD_ALT | MOD_NOREPEAT,
        virtual_key=ord("T"),
        modifier_virtual_keys=(VK_MENU,),
    )
    ocr_spec = HotkeySpec(
        display_name="Alt+Q",
        modifiers=MOD_ALT | MOD_NOREPEAT,
        virtual_key=ord("Q"),
        modifier_virtual_keys=(VK_MENU,),
    )
    window._translation_hotkey_spec = translation_spec
    window._ocr_hotkey_spec = ocr_spec

    window._on_global_hotkey(translation_spec)
    window._on_global_hotkey(ocr_spec)
    application.processEvents()

    assert selection_service.release_keys == (VK_MENU, ord("T"))
    assert screenshot_service.capture_calls == 1
    assert window._capture_overlay is not None
    assert window._capture_overlay.isVisible()
    window._capture_overlay.close()
    window._selection_service = None
    window.close()


def test_capture_stays_in_memory_unless_debug_is_enabled(application):
    image = QImage(80, 40, QImage.Format.Format_ARGB32)
    image.fill("red")

    normal_service = FakeScreenshotService()
    normal_ocr = FakeOCRController()
    normal_client = FakeClient(result="OCR translation", delay=0.01)
    normal_window = MainWindow(
        DEFAULT_SETTINGS,
        client_factory=lambda settings: normal_client,
        screenshot_service=normal_service,
        ocr_controller_factory=lambda: normal_ocr,
    )
    normal_window._on_area_captured(image)

    assert normal_window._last_capture is not image
    assert normal_window._last_capture.size() == image.size()
    assert normal_service.saved_images == []
    assert len(normal_ocr.images) == 1
    assert normal_window.status_label.text() == "Recognizing text..."
    assert normal_window.popup.body_edit.toPlainText() == "Recognizing text..."
    popup_id = int(normal_window.popup.winId())
    normal_ocr.text_ready.emit(1, "recognized text")
    wait_until(application, normal_window.translate_button.isEnabled)
    assert normal_window.source_edit.toPlainText() == "recognized text"
    assert normal_window._last_capture is None
    assert normal_client.calls == ["recognized text"]
    assert normal_window.result_edit.toPlainText() == "OCR translation"
    assert normal_window.popup.result_text == "OCR translation"
    assert int(normal_window.popup.winId()) == popup_id
    normal_window.close()

    debug_service = FakeScreenshotService()
    debug_ocr = FakeOCRController()
    created_clients = []
    debug_window = MainWindow(
        replace(DEFAULT_SETTINGS, debug=True),
        client_factory=lambda settings: created_clients.append(settings),
        screenshot_service=debug_service,
        ocr_controller_factory=lambda: debug_ocr,
    )
    debug_window._on_area_captured(image)

    assert len(debug_service.saved_images) == 1
    assert debug_service.saved_images[0].size() == image.size()
    debug_ocr.no_text.emit(1)
    application.processEvents()
    assert debug_window.status_label.text() == "No text was detected in this area."
    assert debug_window.popup.body_edit.toPlainText() == (
        "No text was detected in this area."
    )
    assert created_clients == []
    debug_window.close()


def test_invalid_capture_error_does_not_start_deepseek(application):
    image = QImage(80, 40, QImage.Format.Format_ARGB32)
    image.fill("black")
    ocr = FakeOCRController()
    created_clients = []
    window = MainWindow(
        DEFAULT_SETTINGS,
        client_factory=lambda settings: created_clients.append(settings),
        screenshot_service=FakeScreenshotService(),
        ocr_controller_factory=lambda: ocr,
    )

    window._on_area_captured(image)
    ocr.failed.emit(1, INVALID_CAPTURE_MESSAGE)
    application.processEvents()

    assert created_clients == []
    assert window.status_label.text() == INVALID_CAPTURE_MESSAGE
    assert window.popup.body_edit.toPlainText() == INVALID_CAPTURE_MESSAGE
    window.close()


def test_new_selected_text_invalidates_pending_ocr(application):
    image = QImage(80, 40, QImage.Format.Format_ARGB32)
    image.fill("red")
    ocr = FakeOCRController()
    client = FakeClient(result="new translation", delay=0.01)
    window = MainWindow(
        DEFAULT_SETTINGS,
        client_factory=lambda settings: client,
        screenshot_service=FakeScreenshotService(),
        ocr_controller_factory=lambda: ocr,
    )

    window._on_area_captured(image)
    window._on_selected_text("new selected text", True)
    ocr.text_ready.emit(1, "stale OCR text")
    wait_until(application, window.translate_button.isEnabled)

    assert ocr.invalidate_calls == 1
    assert client.calls == ["new selected text"]
    assert window.source_edit.toPlainText() == "new selected text"
    assert window.popup.result_text == "new translation"
    window.close()


def test_mixed_selected_text_is_translated_to_english(application):
    client = FakeClient(result="Please open Windows settings")
    window = MainWindow(
        DEFAULT_SETTINGS,
        client_factory=lambda settings: client,
    )
    source_text = "请打开 Windows settings"

    window._on_selected_text(source_text, True)
    wait_until(application, lambda: window.translate_button.isEnabled())

    assert client.calls == [source_text]
    assert window.source_edit.toPlainText() == source_text
    assert window.result_edit.toPlainText() == "Please open Windows settings"
    assert window.popup.result_text == "Please open Windows settings"
    assert window.status_label.text() == "Translation complete."
    assert window.translate_button.isEnabled()
    window.close()


def test_mixed_ocr_text_is_translated_to_english(application):
    image = QImage(80, 40, QImage.Format.Format_ARGB32)
    image.fill("red")
    client = FakeClient(result="This feature supports English")
    ocr = FakeOCRController()
    window = MainWindow(
        DEFAULT_SETTINGS,
        client_factory=lambda settings: client,
        screenshot_service=FakeScreenshotService(),
        ocr_controller_factory=lambda: ocr,
    )
    source_text = "本功能 supports English"

    window._on_area_captured(image)
    ocr.text_ready.emit(1, source_text)
    wait_until(application, lambda: window.translate_button.isEnabled())

    assert client.calls == [source_text]
    assert window.source_edit.toPlainText() == source_text
    assert window.result_edit.toPlainText() == "This feature supports English"
    assert window.popup.result_text == "This feature supports English"
    assert window.status_label.text() == "Translation complete."
    assert window.translate_button.isEnabled()
    window.close()


def test_translation_runs_in_worker_and_updates_result(application):
    client = FakeClient(result="后台译文", delay=0.1)
    window = MainWindow(DEFAULT_SETTINGS, client_factory=lambda settings: client)
    window.show()
    window.source_edit.setPlainText("Source text")
    timer_processed = []

    window.translate_button.click()
    QTimer.singleShot(0, lambda: timer_processed.append(True))
    application.processEvents()

    assert window.status_label.text() == "Translating..."
    assert not window.translate_button.isEnabled()
    wait_until(application, lambda: bool(timer_processed))
    wait_until(application, window.translate_button.isEnabled)

    assert client.calls == ["Source text"]
    assert client.closed is True
    assert window.result_edit.toPlainText() == "后台译文"
    assert window.status_label.text() == "Translation complete."
    window.close()


def test_hotkey_translation_updates_one_popup(application):
    client = FakeClient(result="译文", delay=0.01)
    window = MainWindow(DEFAULT_SETTINGS, client_factory=lambda settings: client)

    window._on_selected_text("This is a test.", True)
    first_popup_id = int(window.popup.winId())
    assert window.popup.isVisible()
    assert window.popup.body_edit.toPlainText() == "Translating..."
    wait_until(application, window.translate_button.isEnabled)

    assert int(window.popup.winId()) == first_popup_id
    assert window.popup.result_text == "译文"
    assert client.calls == ["This is a test."]
    window.popup.close()
    assert window._current_source_text is None
    assert window.source_edit.toPlainText() == ""
    assert window.result_edit.toPlainText() == ""
    window.close()


def test_latest_hotkey_request_remains_visible_when_older_finishes_last(application):
    client = OutOfOrderClient()
    window = MainWindow(DEFAULT_SETTINGS, client_factory=lambda settings: client)

    window._on_selected_text("A", True)
    wait_until(application, client.started["A"].is_set)
    window._on_selected_text("B", True)
    wait_until(application, client.started["B"].is_set)

    client.release["B"].set()
    wait_until(application, lambda: window.popup.result_text == "result-B")
    client.release["A"].set()
    wait_until(application, lambda: not window._controller.has_running_requests)

    assert window.popup.result_text == "result-B"
    assert window.result_edit.toPlainText() == "result-B"
    assert window.source_edit.toPlainText() == "B"
    window.popup.close()
    window.close()


def test_empty_source_does_not_create_client(application):
    created = []
    window = MainWindow(
        DEFAULT_SETTINGS,
        client_factory=lambda settings: created.append(settings),
    )

    window.translate_button.click()

    assert created == []
    assert window.status_label.text() == "Enter text to translate."
    assert window.translate_button.isEnabled()
    window.close()


def test_expected_ollama_error_is_shown_without_crashing(application):
    client = FakeClient(
        error=OllamaConnectionError("Could not connect to Ollama."),
    )
    window = MainWindow(DEFAULT_SETTINGS, client_factory=lambda settings: client)
    window.source_edit.setPlainText("Text")

    window.translate_button.click()
    wait_until(application, window.translate_button.isEnabled)

    assert window.result_edit.toPlainText() == ""
    assert window.status_label.text() == "Could not connect to Ollama."
    assert window.isVisible()
    assert client.closed is True
    window.close()


def test_unexpected_worker_error_uses_generic_message(application):
    client = FakeClient(error=RuntimeError("private failure details"))
    window = MainWindow(DEFAULT_SETTINGS, client_factory=lambda settings: client)
    window.source_edit.setPlainText("Text")

    window.translate_button.click()
    wait_until(application, window.translate_button.isEnabled)

    assert window.status_label.text() == "Translation failed unexpectedly."
    assert "private failure details" not in window.status_label.text()
    window.close()


def test_close_is_deferred_while_worker_is_active(application):
    client = FakeClient(delay=0.15)
    window = MainWindow(DEFAULT_SETTINGS, client_factory=lambda settings: client)
    window.show()
    window.source_edit.setPlainText("Text")
    window.translate_button.click()

    closed = window.close()

    assert closed is False
    assert window.isVisible()
    assert "finish before closing" in window.status_label.text()
    wait_until(application, window.translate_button.isEnabled)
    assert window.close() is True


def test_close_hides_to_tray_and_explicit_exit_cleans_up(application):
    window = MainWindow(DEFAULT_SETTINGS, close_to_tray=True)
    exit_events = []
    window.exit_ready.connect(lambda: exit_events.append(True))
    window.show()
    application.processEvents()

    assert window.close() is False
    assert not window.isVisible()
    assert exit_events == []
    assert "system tray" in window.status_label.text()

    window.show_from_background()
    application.processEvents()
    assert window.isVisible()

    window.request_exit()
    application.processEvents()
    assert not window.isVisible()
    assert exit_events == [True]


def test_selected_text_failure_uses_popup_without_reopening_hidden_main_window(
    application,
):
    window = MainWindow(DEFAULT_SETTINGS, close_to_tray=True)
    window.show()
    window.close()
    assert not window.isVisible()

    window._show_selection_error("No selectable text was detected.")
    application.processEvents()

    assert not window.isVisible()
    assert window.popup.isVisible()
    assert window.popup.body_edit.toPlainText() == "No selectable text was detected."
    window.popup.close()
    window.request_exit()


def test_closed_hotkey_popup_error_does_not_reopen_main_window(application):
    client = FakeClient(
        error=OllamaConnectionError("Could not connect to Ollama."),
        delay=0.05,
    )
    window = MainWindow(
        DEFAULT_SETTINGS,
        client_factory=lambda settings: client,
        close_to_tray=True,
    )
    window.show()
    window.close()
    window._on_selected_text("Selected text", True)
    window.popup.close()

    wait_until(application, window.translate_button.isEnabled)

    assert not window.isVisible()
    assert not window.popup.isVisible()
    assert window.status_label.text() == "Translation dismissed."
    window.request_exit()
