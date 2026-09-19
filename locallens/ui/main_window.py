"""Non-blocking development window for exercising translation requests."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable

from PySide6.QtCore import QTimer, Signal, Slot
from PySide6.QtGui import QCloseEvent, QCursor, QImage
from PySide6.QtWidgets import (
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from locallens.autostart import sync_autostart
from locallens.config import Settings, save_settings
from locallens.controller import ClientFactory, TranslationController
from locallens.hotkeys import (
    HotkeyManager,
    HotkeyRegistrationError,
    HotkeySpec,
)
from locallens.logging_config import set_logging_level
from locallens.services.clipboard_service import ClipboardService
from locallens.services.ollama_client import OllamaClient
from locallens.services.ocr_service import (
    NO_TEXT_MESSAGE,
    OCRController,
)
from locallens.services.screenshot_service import ScreenshotError, ScreenshotService
from locallens.services.selection_service import SelectionService
from locallens.ui.capture_overlay import CaptureOverlay
from locallens.ui.app_icon import application_icon
from locallens.ui.popup import TranslationPopup
from locallens.ui.settings_dialog import (
    ModelClientFactory,
    SettingsDialog,
    create_model_list_client,
)


HotkeyManagerFactory = Callable[[int], HotkeyManager]
SettingsSaver = Callable[[Settings], None]
AutostartConfigurer = Callable[[bool], bool]
logger = logging.getLogger("locallens.ui")


class MainWindow(QMainWindow):
    """Settings window coordinating global hotkeys and the translation popup."""

    translation_finished = Signal()
    exit_ready = Signal()

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory = OllamaClient,
        screenshot_service: ScreenshotService | None = None,
        ocr_controller_factory: Callable[[], OCRController] = OCRController,
        hotkey_manager_factory: HotkeyManagerFactory = HotkeyManager,
        settings_saver: SettingsSaver = save_settings,
        autostart_configurer: AutostartConfigurer = sync_autostart,
        model_client_factory: ModelClientFactory = create_model_list_client,
        close_to_tray: bool = False,
    ) -> None:
        super().__init__()
        self._settings = settings
        self._controller = TranslationController(
            settings, client_factory=client_factory, parent=self
        )
        self._controller.translation_succeeded.connect(self._show_translation)
        self._controller.translation_bypassed.connect(self._show_bypassed_source)
        self._controller.translation_failed.connect(self._show_error)
        self._controller.request_finished.connect(self._on_request_finished)
        self._hotkey_manager: HotkeyManager | None = None
        self._translation_hotkey_spec: HotkeySpec | None = None
        self._ocr_hotkey_spec: HotkeySpec | None = None
        self._selection_service: SelectionService | None = None
        self._screenshot_service = screenshot_service or ScreenshotService()
        if not settings.debug:
            self._screenshot_service.remove_debug_capture()
        self._ocr_controller_factory = ocr_controller_factory
        self._hotkey_manager_factory = hotkey_manager_factory
        self._settings_saver = settings_saver
        self._autostart_configurer = autostart_configurer
        self._model_client_factory = model_client_factory
        self._close_to_tray = close_to_tray
        self._exit_requested = False
        self._exit_completed = False
        self._ocr_controller: OCRController | None = None
        self._active_ocr_request_id: int | None = None
        self._capture_overlay: CaptureOverlay | None = None
        self._last_capture: QImage | None = None
        self._translation_output = "main"
        self._active_clipboard_restore_skipped = False
        self._current_source_text: str | None = None
        self._settings_dialog: SettingsDialog | None = None
        self._translation_hotkey_started_at: float | None = None

        self.popup = TranslationPopup()
        self.popup.dismissed.connect(self._on_popup_dismissed)

        self.setWindowTitle("LocalLens")
        self.setWindowIcon(application_icon())
        self.resize(720, 560)

        self.source_edit = QTextEdit()
        self.source_edit.setObjectName("sourceEdit")
        self.source_edit.setPlaceholderText("Enter source text...")
        self.source_edit.setAcceptRichText(False)

        self.translate_button = QPushButton("Translate")
        self.translate_button.setObjectName("translateButton")
        self.translate_button.clicked.connect(self.start_translation)

        self.settings_button = QPushButton("Settings")
        self.settings_button.setObjectName("settingsButton")
        self.settings_button.clicked.connect(self.open_settings)

        controls = QHBoxLayout()
        controls.addStretch(1)
        controls.addWidget(self.settings_button)
        controls.addWidget(self.translate_button)

        self.status_label = QLabel("Ready")
        self.status_label.setObjectName("statusLabel")

        self.result_edit = QTextEdit()
        self.result_edit.setObjectName("resultEdit")
        self.result_edit.setReadOnly(True)
        self.result_edit.setPlaceholderText("Translation result")

        form = QFormLayout()
        form.addRow("Source text:", self.source_edit)
        form.addRow("Result:", self.result_edit)

        layout = QVBoxLayout()
        layout.addLayout(form, 1)
        layout.addLayout(controls)
        layout.addWidget(self.status_label)

        container = QWidget()
        container.setLayout(layout)
        self.setCentralWidget(container)
        self.source_edit.setFocus()

    def start_hotkeys(self) -> bool:
        """Register the configured translation hotkey after the window exists."""

        if self._hotkey_manager is not None:
            return True

        try:
            self._install_hotkeys(self._settings)
        except HotkeyRegistrationError as exc:
            logger.warning("hotkey_startup_failed")
            self.status_label.setText(str(exc))
            return False

        self._show_ready_status()
        return True

    def _install_hotkeys(self, settings: Settings) -> None:
        hotkey_manager = self._hotkey_manager_factory(int(self.winId()))
        selection_service = SelectionService(ClipboardService())
        hotkey_manager.activated.connect(self._on_global_hotkey)
        selection_service.started.connect(self._on_selection_started)
        selection_service.text_ready.connect(self._on_selected_text)
        selection_service.failed.connect(self._show_selection_error)
        try:
            translation_spec = hotkey_manager.register(settings.translation_hotkey)
            ocr_spec = hotkey_manager.register(settings.ocr_hotkey)
        except HotkeyRegistrationError:
            hotkey_manager.unregister_all()
            raise

        self._hotkey_manager = hotkey_manager
        self._translation_hotkey_spec = translation_spec
        self._ocr_hotkey_spec = ocr_spec
        self._selection_service = selection_service

    def _show_ready_status(self) -> None:
        if self._translation_hotkey_spec is None or self._ocr_hotkey_spec is None:
            self.status_label.setText("Ready")
            return
        self.status_label.setText(
            f"Ready - {self._translation_hotkey_spec.display_name} translates "
            f"selected text; {self._ocr_hotkey_spec.display_name} captures an area"
        )

    def stop_hotkeys(self) -> None:
        if self._capture_overlay is not None:
            self._capture_overlay.close()
        if self._selection_service is not None:
            self._selection_service.cancel()
        if self._hotkey_manager is not None:
            self._hotkey_manager.unregister_all()
        self._selection_service = None
        self._hotkey_manager = None
        self._translation_hotkey_spec = None
        self._ocr_hotkey_spec = None

    @Slot()
    def open_settings(self) -> None:
        if self._settings_dialog is None:
            dialog = SettingsDialog(
                self._settings,
                model_client_factory=self._model_client_factory,
                parent=self,
            )
            dialog.save_requested.connect(
                lambda settings, dialog=dialog: self._apply_settings(dialog, settings)
            )
            self._settings_dialog = dialog
        self._settings_dialog.show()
        self._settings_dialog.raise_()
        self._settings_dialog.activateWindow()

    @Slot()
    def show_from_background(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def set_close_to_tray(self, enabled: bool) -> None:
        self._close_to_tray = bool(enabled)

    @Slot()
    def request_exit(self) -> None:
        self._exit_requested = True
        self.close()

    def _apply_settings(
        self, dialog: SettingsDialog, candidate: Settings
    ) -> None:
        if self._controller.has_running_requests:
            dialog.show_save_error(
                "Wait for the current translation to finish before saving settings."
            )
            return
        if self._active_ocr_request_id is not None:
            dialog.show_save_error(
                "Wait for the current OCR operation to finish before saving settings."
            )
            return
        if self._capture_overlay is not None and self._capture_overlay.isVisible():
            dialog.show_save_error(
                "Finish or cancel the current screen capture before saving settings."
            )
            return

        previous = self._settings
        hotkeys_were_active = self._hotkey_manager is not None
        if hotkeys_were_active:
            self.stop_hotkeys()
        try:
            self._install_hotkeys(candidate)
        except HotkeyRegistrationError as exc:
            if hotkeys_were_active:
                rollback_error = self._restore_hotkeys(previous)
            else:
                rollback_error = None
            message = str(exc)
            if rollback_error is not None:
                message += f" Previous hotkeys could not be restored: {rollback_error}"
            dialog.show_save_error(message)
            return

        autostart_changed = (
            candidate.start_with_windows != previous.start_with_windows
        )
        try:
            if autostart_changed:
                self._autostart_configurer(candidate.start_with_windows)
            self._settings_saver(candidate)
        except Exception as exc:
            logger.error(
                "settings_save_failed exception_type=%s",
                type(exc).__name__,
            )
            if autostart_changed:
                try:
                    self._autostart_configurer(previous.start_with_windows)
                except Exception as rollback_exc:
                    logger.error(
                        "autostart_rollback_failed exception_type=%s",
                        type(rollback_exc).__name__,
                    )
            self.stop_hotkeys()
            if hotkeys_were_active:
                rollback_error = self._restore_hotkeys(previous)
            else:
                rollback_error = None
            message = "Settings could not be saved. The previous settings are still active."
            if rollback_error is not None:
                message += f" Previous hotkeys could not be restored: {rollback_error}"
            dialog.show_save_error(message)
            return

        self._settings = candidate
        if not candidate.debug:
            self._screenshot_service.remove_debug_capture()
        self._controller.update_settings(candidate)
        set_logging_level(candidate.debug)
        self.status_label.setText("Settings saved and applied.")
        logger.info("settings_saved model=%s", candidate.model)
        dialog.finish_save(candidate)

    def _restore_hotkeys(self, settings: Settings) -> str | None:
        try:
            self._install_hotkeys(settings)
        except HotkeyRegistrationError as exc:
            return str(exc)
        return None

    @Slot(object)
    def _on_global_hotkey(self, spec: HotkeySpec) -> None:
        if spec == self._translation_hotkey_spec:
            logger.info("hotkey_action action=translation")
            self._translation_hotkey_started_at = time.monotonic()
            self._on_translation_hotkey(spec)
        elif spec == self._ocr_hotkey_spec:
            logger.info("hotkey_action action=ocr")
            started_at = time.monotonic()
            self._on_ocr_hotkey(spec)
            if self._capture_overlay is not None and self._capture_overlay.isVisible():
                logger.info(
                    "hotkey_to_overlay action=ocr duration_ms=%d",
                    round((time.monotonic() - started_at) * 1000),
                )

    @Slot(object)
    def _on_translation_hotkey(self, spec: HotkeySpec) -> None:
        if self._selection_service is None:
            self.status_label.setText("Selected-text capture is unavailable.")
            return
        self._selection_service.capture_selected_text(
            (*spec.modifier_virtual_keys, spec.virtual_key)
        )

    @Slot(object)
    def _on_ocr_hotkey(self, spec: HotkeySpec) -> None:
        if self._capture_overlay is not None and self._capture_overlay.isVisible():
            self.status_label.setText("Screen capture is already in progress.")
            return
        try:
            capture = self._screenshot_service.capture_current_screen(QCursor.pos())
        except ScreenshotError as exc:
            self._show_selection_error(str(exc))
            return

        overlay = CaptureOverlay(capture)
        overlay.captured.connect(self._on_area_captured)
        overlay.cancelled.connect(self._on_capture_cancelled)
        overlay.empty_selection.connect(self._on_empty_capture_selection)
        self._capture_overlay = overlay
        self._last_capture = None
        self.status_label.setText("Drag to select an area. Press Esc to cancel.")
        overlay.start()

    @Slot(object)
    def _on_area_captured(self, image: QImage) -> None:
        self._capture_overlay = None
        self._last_capture = image.copy()
        if self._settings.debug:
            try:
                self._screenshot_service.save_debug_capture(self._last_capture)
            except ScreenshotError as exc:
                self._last_capture = None
                self.status_label.setText(str(exc))
                return
        self._controller.invalidate_active_request()
        self.translate_button.setEnabled(True)
        self._current_source_text = None
        self.popup.show_recognizing(
            anchor=QCursor.pos(),
        )
        controller = self._ensure_ocr_controller()
        self.status_label.setText("Recognizing text...")
        self._active_ocr_request_id = controller.recognize(self._last_capture)

    def _ensure_ocr_controller(self) -> OCRController:
        if self._ocr_controller is None:
            controller = self._ocr_controller_factory()
            controller.text_ready.connect(self._on_ocr_text_ready)
            controller.no_text.connect(self._on_ocr_no_text)
            controller.failed.connect(self._on_ocr_failed)
            self._ocr_controller = controller
        return self._ocr_controller

    @Slot(int, str)
    def _on_ocr_text_ready(self, request_id: int, text: str) -> None:
        if request_id != self._active_ocr_request_id:
            return
        self._active_ocr_request_id = None
        self._last_capture = None
        self._current_source_text = text
        self.source_edit.setPlainText(text)
        self.popup.show_translating()
        self._begin_translation(text, output="popup")

    @Slot(int)
    def _on_ocr_no_text(self, request_id: int) -> None:
        if request_id != self._active_ocr_request_id:
            return
        self._active_ocr_request_id = None
        self._last_capture = None
        logger.warning("ocr_result status=no_text")
        self.status_label.setText(NO_TEXT_MESSAGE)
        self.popup.show_error(NO_TEXT_MESSAGE)

    @Slot(int, str)
    def _on_ocr_failed(self, request_id: int, message: str) -> None:
        if request_id != self._active_ocr_request_id:
            return
        self._active_ocr_request_id = None
        self._last_capture = None
        logger.warning("ocr_result status=failed")
        self.result_edit.clear()
        self.status_label.setText(message)
        self.popup.show_error(message)

    @Slot()
    def _on_capture_cancelled(self) -> None:
        self._capture_overlay = None
        self.status_label.setText("Screen capture cancelled.")

    @Slot()
    def _on_empty_capture_selection(self) -> None:
        self.status_label.setText("Drag to select a non-empty area.")

    @Slot()
    def _on_selection_started(self) -> None:
        self.status_label.setText("Reading selected text...")
        self.popup.show_reading_selection(
            anchor=QCursor.pos(),
        )
        if self._translation_hotkey_started_at is not None:
            logger.info(
                "hotkey_to_popup action=translation duration_ms=%d",
                round(
                    (time.monotonic() - self._translation_hotkey_started_at) * 1000
                ),
            )
            self._translation_hotkey_started_at = None

    @Slot(str, bool)
    def _on_selected_text(self, source_text: str, clipboard_restored: bool) -> None:
        self._invalidate_pending_ocr()
        self._current_source_text = source_text
        self.source_edit.setPlainText(source_text)
        self.popup.show_translating(anchor=QCursor.pos())
        self._begin_translation(
            source_text,
            output="popup",
            clipboard_restore_skipped=not clipboard_restored,
        )

    @Slot()
    def start_translation(self) -> None:
        self._invalidate_pending_ocr()
        source_text = self.source_edit.toPlainText()
        if not source_text.strip():
            self.status_label.setText("Enter text to translate.")
            return

        self._begin_translation(source_text, output="main")

    def _begin_translation(
        self,
        source_text: str,
        *,
        output: str,
        clipboard_restore_skipped: bool = False,
    ) -> int:
        self._translation_output = output
        self._active_clipboard_restore_skipped = clipboard_restore_skipped
        self.status_label.setText("Translating...")
        self.translate_button.setEnabled(False)
        return self._controller.start_translation(source_text)

    @Slot()
    def _on_popup_dismissed(self) -> None:
        self._current_source_text = None
        self._invalidate_pending_ocr()
        if self._translation_output == "popup":
            self._controller.invalidate_active_request()
            self.source_edit.clear()
            self.result_edit.clear()
            if not self._controller.has_running_requests:
                self.translate_button.setEnabled(True)
            self.status_label.setText("Translation dismissed.")

    def _invalidate_pending_ocr(self) -> None:
        if self._active_ocr_request_id is None:
            return
        self._active_ocr_request_id = None
        self._last_capture = None
        if self._ocr_controller is not None:
            self._ocr_controller.invalidate_active_request()

    @Slot(int, str)
    def _show_translation(self, request_id: int, translation: str) -> None:
        self.result_edit.setPlainText(translation)
        if self._active_clipboard_restore_skipped:
            self.status_label.setText(
                "Translation complete. Newer clipboard content was preserved."
            )
        else:
            self.status_label.setText("Translation complete.")
        self._active_clipboard_restore_skipped = False
        if self._translation_output == "popup" and self.popup.isVisible():
            self.popup.show_translation(translation)

    @Slot(int, str)
    def _show_bypassed_source(self, request_id: int, source_text: str) -> None:
        self.result_edit.setPlainText(source_text)
        self.status_label.setText(
            "Mixed Chinese and English detected. Original text returned "
            "without using DeepSeek."
        )
        self._active_clipboard_restore_skipped = False
        if self._translation_output == "popup" and self.popup.isVisible():
            self.popup.show_translation(source_text)

    @Slot(int, str)
    def _show_error(self, request_id: int, message: str) -> None:
        logger.warning("translation_error_presented output=%s", self._translation_output)
        self.result_edit.clear()
        self.status_label.setText(message)
        if self._translation_output == "popup":
            if self.popup.isVisible():
                self.popup.show_error(message)
            return
        self.showNormal()
        self.raise_()
        self.activateWindow()

    @Slot(str)
    def _show_selection_error(self, message: str) -> None:
        self._translation_hotkey_started_at = None
        logger.warning("capture_error_presented source=selection_or_screenshot")
        self.result_edit.clear()
        self.status_label.setText(message)
        self.popup.show_error(
            message,
            anchor=QCursor.pos(),
        )

    @Slot(int)
    def _on_request_finished(self, request_id: int) -> None:
        if request_id == self._controller.active_request_id or (
            self._controller.active_request_id is None
            and not self._controller.has_running_requests
        ):
            self.translate_button.setEnabled(True)
            self.translation_finished.emit()
        if self._exit_requested and not self._controller.has_running_requests:
            QTimer.singleShot(0, self.close)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self._close_to_tray and not self._exit_requested:
            if self._settings_dialog is not None:
                self._settings_dialog.hide()
            self.hide()
            self.status_label.setText("LocalLens is running in the system tray.")
            event.ignore()
            return
        if self._controller.has_running_requests:
            self.status_label.setText(
                "Wait for the current translation to finish before closing."
            )
            self.show_from_background()
            event.ignore()
            return
        if (
            self._settings_dialog is not None
            and self._settings_dialog.is_refreshing_models
        ):
            self.status_label.setText(
                "Wait for the Ollama model list to finish loading before closing."
            )
            self.show_from_background()
            event.ignore()
            return
        if self._ocr_controller is not None and not self._ocr_controller.shutdown():
            self.status_label.setText(
                "Wait for the current OCR operation to finish before closing."
            )
            self.show_from_background()
            event.ignore()
            return
        self.popup.close()
        self.stop_hotkeys()
        super().closeEvent(event)
        if not self._exit_completed:
            self._exit_completed = True
            self.exit_ready.emit()
