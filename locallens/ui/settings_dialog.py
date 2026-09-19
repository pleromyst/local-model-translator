"""Settings dialog with non-blocking Ollama model discovery."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QObject, QThread, Qt, Signal, Slot
from PySide6.QtGui import QCloseEvent, QKeyEvent, QShowEvent
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
)

from locallens.config import ConfigurationError, Settings
from locallens.hotkeys import parse_hotkey
from locallens.services.ollama_client import OllamaClient, OllamaError


class ModelListClient:
    """Structural documentation for model-list client factories."""

    def list_models(self) -> list[str]: ...

    def close(self) -> None: ...


ModelClientFactory = Callable[[Settings], ModelListClient]


def create_model_list_client(settings: Settings) -> OllamaClient:
    """Use short discovery timeouts so a stopped Ollama cannot stall shutdown."""

    return OllamaClient(settings, timeout=(2.0, 10.0))


class HotkeyCaptureEdit(QLineEdit):
    """Read-only field that records one supported modifier/key combination."""

    def __init__(self, hotkey: str, parent=None) -> None:
        super().__init__(hotkey, parent)
        self.setReadOnly(True)
        self.setPlaceholderText("Click here, then press a shortcut")
        self.setToolTip(
            "Click this field and press a modifier plus a letter, number, or F1-F24."
        )
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        key = event.key()
        modifier_keys = {
            int(Qt.Key.Key_Control),
            int(Qt.Key.Key_Alt),
            int(Qt.Key.Key_Shift),
            int(Qt.Key.Key_Meta),
        }
        if key in modifier_keys or event.isAutoRepeat():
            event.accept()
            return

        modifiers = event.modifiers()
        modifier_names: list[str] = []
        for flag, name in (
            (Qt.KeyboardModifier.ControlModifier, "Ctrl"),
            (Qt.KeyboardModifier.AltModifier, "Alt"),
            (Qt.KeyboardModifier.ShiftModifier, "Shift"),
            (Qt.KeyboardModifier.MetaModifier, "Win"),
        ):
            if modifiers & flag:
                modifier_names.append(name)
        if not modifier_names:
            event.accept()
            return

        if int(Qt.Key.Key_A) <= key <= int(Qt.Key.Key_Z):
            key_name = chr(key)
        elif int(Qt.Key.Key_0) <= key <= int(Qt.Key.Key_9):
            key_name = chr(key)
        elif int(Qt.Key.Key_F1) <= key <= int(Qt.Key.Key_F24):
            key_name = f"F{key - int(Qt.Key.Key_F1) + 1}"
        else:
            event.accept()
            return

        self.setText("+".join([*modifier_names, key_name]))
        event.accept()


class _ModelListWorker(QObject):
    loaded = Signal(list)
    failed = Signal(str)
    completed = Signal()

    def __init__(
        self, settings: Settings, client_factory: ModelClientFactory
    ) -> None:
        super().__init__()
        self._settings = settings
        self._client_factory = client_factory

    @Slot()
    def run(self) -> None:
        client: ModelListClient | None = None
        try:
            client = self._client_factory(self._settings)
            models = client.list_models()
        except OllamaError as exc:
            self.failed.emit(str(exc))
        except Exception:
            self.failed.emit("Could not load the Ollama model list.")
        else:
            self.loaded.emit(models)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            self.completed.emit()


class SettingsDialog(QDialog):
    """Edit validated settings and request that the main window apply them."""

    save_requested = Signal(object)

    def __init__(
        self,
        settings: Settings,
        *,
        model_client_factory: ModelClientFactory = create_model_list_client,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self._initial_settings = settings
        self._model_client_factory = model_client_factory
        self._model_thread: QThread | None = None
        self._model_worker: _ModelListWorker | None = None
        self._refresh_started = False

        self.setWindowTitle("LocalLens Settings")
        self.setModal(False)
        self.resize(560, 470)

        self.ollama_url_edit = QLineEdit(settings.ollama_url)
        self.ollama_url_edit.setObjectName("ollamaUrlEdit")

        self.model_combo = QComboBox()
        self.model_combo.setObjectName("modelCombo")
        self.model_combo.setEditable(True)
        self.model_combo.addItem(settings.model)
        self.model_combo.setCurrentText(settings.model)
        self.model_combo.setToolTip(
            "Models are loaded from Ollama. LocalLens never switches models "
            "automatically."
        )
        self.refresh_models_button = QPushButton("Refresh models")
        self.refresh_models_button.setObjectName("refreshModelsButton")
        self.refresh_models_button.clicked.connect(self.refresh_models)
        model_row = QHBoxLayout()
        model_row.addWidget(self.model_combo, 1)
        model_row.addWidget(self.refresh_models_button)

        self.target_language_combo = QComboBox()
        self.target_language_combo.setObjectName("targetLanguageCombo")
        self.target_language_combo.setEditable(True)
        self.target_language_combo.addItems(["Simplified Chinese", "English"])
        self.target_language_combo.setCurrentText(settings.target_language)
        self.target_language_combo.setToolTip(
            "Fallback only. English-only and Chinese-only text use automatic "
            "direction detection."
        )

        self.translation_hotkey_edit = HotkeyCaptureEdit(
            settings.translation_hotkey
        )
        self.translation_hotkey_edit.setObjectName("translationHotkeyEdit")
        self.ocr_hotkey_edit = HotkeyCaptureEdit(settings.ocr_hotkey)
        self.ocr_hotkey_edit.setObjectName("ocrHotkeyEdit")
        self.hotkey_help_label = QLabel(
            "Click a hotkey field, then press the desired combination, such as "
            "Ctrl+F8."
        )
        self.hotkey_help_label.setWordWrap(True)

        self.max_chunk_spin = QSpinBox()
        self.max_chunk_spin.setObjectName("maxChunkSpin")
        self.max_chunk_spin.setRange(1, 1_000_000)
        self.max_chunk_spin.setValue(settings.max_chars_per_chunk)

        self.keep_alive_edit = QLineEdit(settings.ollama_keep_alive)
        self.keep_alive_edit.setObjectName("keepAliveEdit")

        self.release_model_checkbox = QCheckBox(
            "Release the model after each translation (recommended while gaming)"
        )
        self.release_model_checkbox.setObjectName("releaseModelCheckBox")
        self.release_model_checkbox.setChecked(
            settings.release_model_after_translation
        )
        self.release_model_checkbox.setToolTip(
            "Releases Ollama model memory after a translation finishes. "
            "The model still uses compute resources while translating."
        )
        self.keep_alive_edit.setEnabled(
            not settings.release_model_after_translation
        )
        self.release_model_checkbox.toggled.connect(
            lambda checked: self.keep_alive_edit.setEnabled(not checked)
        )

        self.start_with_windows_checkbox = QCheckBox(
            "Start LocalLens in the background when I sign in"
        )
        self.start_with_windows_checkbox.setObjectName("startWithWindowsCheckBox")
        self.start_with_windows_checkbox.setChecked(settings.start_with_windows)
        self.start_with_windows_checkbox.setToolTip(
            "Starts LocalLens without opening the main window. "
            "The translation and OCR hotkeys remain available."
        )

        self.debug_checkbox = QCheckBox("Save the latest OCR capture for debugging")
        self.debug_checkbox.setObjectName("debugCheckBox")
        self.debug_checkbox.setChecked(settings.debug)

        form = QFormLayout()
        form.addRow("Ollama URL:", self.ollama_url_edit)
        form.addRow("Ollama model:", model_row)
        form.addRow("Target language:", self.target_language_combo)
        form.addRow("Translation hotkey:", self.translation_hotkey_edit)
        form.addRow("OCR hotkey:", self.ocr_hotkey_edit)
        form.addRow("", self.hotkey_help_label)
        form.addRow("Max chunk size:", self.max_chunk_spin)
        form.addRow("GPU memory:", self.release_model_checkbox)
        form.addRow("Idle retention when disabled:", self.keep_alive_edit)
        form.addRow("Sign-in startup:", self.start_with_windows_checkbox)
        form.addRow("Debug:", self.debug_checkbox)

        self.feedback_label = QLabel("Models will be loaded from Ollama.")
        self.feedback_label.setObjectName("settingsFeedbackLabel")
        self.feedback_label.setWordWrap(True)

        self.save_button = QPushButton("Save")
        self.save_button.setObjectName("saveSettingsButton")
        self.save_button.clicked.connect(self._request_save)
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        buttons.addStretch(1)
        buttons.addWidget(self.cancel_button)
        buttons.addWidget(self.save_button)

        layout = QVBoxLayout()
        layout.addLayout(form)
        layout.addWidget(self.feedback_label)
        layout.addStretch(1)
        layout.addLayout(buttons)
        self.setLayout(layout)

    @property
    def is_refreshing_models(self) -> bool:
        return self._model_thread is not None and self._model_thread.isRunning()

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        self.save_button.setEnabled(True)
        if not self._refresh_started:
            self._refresh_started = True
            self.refresh_models()

    def candidate_settings(self) -> Settings:
        mapping = self._initial_settings.to_dict()
        mapping.update(
            {
                "ollama_url": self.ollama_url_edit.text(),
                "model": self.model_combo.currentText(),
                "target_language": self.target_language_combo.currentText(),
                "translation_hotkey": self.translation_hotkey_edit.text(),
                "ocr_hotkey": self.ocr_hotkey_edit.text(),
                "max_chars_per_chunk": self.max_chunk_spin.value(),
                "ollama_keep_alive": self.keep_alive_edit.text(),
                "release_model_after_translation": (
                    self.release_model_checkbox.isChecked()
                ),
                "start_with_windows": self.start_with_windows_checkbox.isChecked(),
                "debug": self.debug_checkbox.isChecked(),
            }
        )
        settings = Settings.from_mapping(mapping)
        translation_spec = parse_hotkey(settings.translation_hotkey)
        ocr_spec = parse_hotkey(settings.ocr_hotkey)
        if translation_spec == ocr_spec:
            raise ConfigurationError(
                "Translation and OCR hotkeys must be different."
            )
        return settings

    @Slot()
    def refresh_models(self) -> None:
        if self._model_thread is not None:
            return
        try:
            mapping = self._initial_settings.to_dict()
            mapping["ollama_url"] = self.ollama_url_edit.text()
            mapping["model"] = (
                self.model_combo.currentText().strip() or self._initial_settings.model
            )
            settings = Settings.from_mapping(mapping)
        except (ConfigurationError, TypeError, ValueError) as exc:
            self.show_save_error(str(exc))
            return

        self.refresh_models_button.setEnabled(False)
        self.feedback_label.setText("Loading models from Ollama...")
        thread = QThread(self)
        worker = _ModelListWorker(settings, self._model_client_factory)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.loaded.connect(self._apply_model_names)
        worker.failed.connect(self.show_save_error)
        worker.completed.connect(worker.deleteLater)
        worker.completed.connect(thread.quit)
        thread.finished.connect(self._on_model_refresh_finished)
        self._model_thread = thread
        self._model_worker = worker
        thread.start()

    @Slot(list)
    def _apply_model_names(self, models: list[str]) -> None:
        current_model = self.model_combo.currentText().strip()
        unique_models = list(dict.fromkeys(model for model in models if model.strip()))
        self.model_combo.clear()
        self.model_combo.addItems(unique_models)
        if current_model and current_model not in unique_models:
            self.model_combo.insertItem(0, current_model)
        self.model_combo.setCurrentText(current_model)
        if unique_models:
            self.feedback_label.setText(
                f"Loaded {len(unique_models)} model(s). Current model was preserved."
            )
        else:
            self.feedback_label.setText(
                "Ollama returned no installed models. Current model was preserved."
            )

    @Slot()
    def _on_model_refresh_finished(self) -> None:
        thread = self._model_thread
        if thread is not None:
            thread.wait()
            thread.deleteLater()
        self._model_thread = None
        self._model_worker = None
        self.refresh_models_button.setEnabled(True)

    @Slot()
    def _request_save(self) -> None:
        try:
            settings = self.candidate_settings()
        except (ConfigurationError, TypeError, ValueError) as exc:
            self.show_save_error(str(exc))
            return
        self.save_button.setEnabled(False)
        self.feedback_label.setText("Checking hotkeys and saving settings...")
        self.save_requested.emit(settings)

    def show_save_error(self, message: str) -> None:
        self.feedback_label.setText(message)
        self.save_button.setEnabled(True)

    def finish_save(self, settings: Settings | None = None) -> None:
        if settings is not None:
            self._initial_settings = settings
        self.feedback_label.setText("Settings saved.")
        self.save_button.setEnabled(True)
        self.accept()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        # A short tags request is allowed to finish before the dialog is destroyed.
        if self._model_thread is not None and self._model_thread.isRunning():
            self.hide()
            event.ignore()
            return
        super().closeEvent(event)
