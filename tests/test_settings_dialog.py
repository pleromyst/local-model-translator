import os
import time
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QObject, Signal
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication

from locallens.config import DEFAULT_SETTINGS
from locallens.hotkeys import HotkeyRegistrationError, parse_hotkey
from locallens.ui.main_window import MainWindow
from locallens.ui.settings_dialog import HotkeyCaptureEdit, SettingsDialog


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


class FakeModelClient:
    def __init__(self, models):
        self.models = models
        self.closed = False

    def list_models(self):
        return list(self.models)

    def close(self):
        self.closed = True


class _ManagerSignals(QObject):
    activated = Signal(object)


class FakeHotkeyManager:
    def __init__(self, rejected_hotkey=None):
        self.signals = _ManagerSignals()
        self.activated = self.signals.activated
        self.rejected_hotkey = rejected_hotkey
        self.registered = []
        self.unregistered = False

    def register(self, value):
        spec = parse_hotkey(value)
        if value == self.rejected_hotkey:
            raise HotkeyRegistrationError(f"The hotkey {value} is already in use.")
        if spec in self.registered:
            raise HotkeyRegistrationError("duplicate")
        self.registered.append(spec)
        return spec

    def unregister_all(self):
        self.unregistered = True


def test_dialog_collects_every_stage_12_setting(application):
    dialog = SettingsDialog(DEFAULT_SETTINGS)
    dialog.ollama_url_edit.setText("http://localhost:12345/")
    dialog.model_combo.setCurrentText("deepseek-r1:14b")
    dialog.target_language_combo.setCurrentText("English")
    dialog.translation_hotkey_edit.setText("Ctrl+F8")
    dialog.ocr_hotkey_edit.setText("Ctrl+F9")
    dialog.max_chunk_spin.setValue(4567)
    assert dialog.release_model_checkbox.isChecked()
    assert not dialog.keep_alive_edit.isEnabled()
    assert dialog.start_with_windows_checkbox.isChecked()
    dialog.release_model_checkbox.setChecked(False)
    dialog.keep_alive_edit.setText("20m")
    dialog.start_with_windows_checkbox.setChecked(False)
    dialog.debug_checkbox.setChecked(True)

    settings = dialog.candidate_settings()

    assert settings.ollama_url == "http://localhost:12345"
    assert settings.model == "deepseek-r1:14b"
    assert settings.target_language == "English"
    assert settings.translation_hotkey == "Ctrl+F8"
    assert settings.ocr_hotkey == "Ctrl+F9"
    assert settings.max_chars_per_chunk == 4567
    assert settings.ollama_keep_alive == "20m"
    assert settings.release_model_after_translation is False
    assert settings.start_with_windows is False
    assert settings.debug is True
    dialog.close()


def test_gaming_release_toggle_controls_idle_retention_field(application):
    dialog = SettingsDialog(DEFAULT_SETTINGS)

    assert not dialog.keep_alive_edit.isEnabled()
    dialog.release_model_checkbox.setChecked(False)

    assert dialog.keep_alive_edit.isEnabled()
    dialog.close()


def test_dialog_rejects_duplicate_local_hotkeys(application):
    dialog = SettingsDialog(DEFAULT_SETTINGS)
    dialog.translation_hotkey_edit.setText("Control+F8")
    dialog.ocr_hotkey_edit.setText("Ctrl+F8")

    with pytest.raises(ValueError, match="must be different"):
        dialog.candidate_settings()
    dialog.close()


def test_hotkeys_are_captured_from_key_combinations(application):
    translation = HotkeyCaptureEdit("Alt+T")
    ocr = HotkeyCaptureEdit("Alt+Q")
    translation.show()
    ocr.show()

    translation.setFocus()
    QTest.keyClick(
        translation,
        Qt.Key.Key_F8,
        Qt.KeyboardModifier.ControlModifier,
    )
    ocr.setFocus()
    QTest.keyClick(
        ocr,
        Qt.Key.Key_F9,
        Qt.KeyboardModifier.ControlModifier,
    )
    application.processEvents()

    assert translation.text() == "Ctrl+F8"
    assert ocr.text() == "Ctrl+F9"
    assert translation.isReadOnly()
    translation.close()
    ocr.close()


def test_save_button_is_reenabled_for_a_second_save(application):
    dialog = SettingsDialog(DEFAULT_SETTINGS)
    requested = []
    dialog.save_requested.connect(requested.append)

    dialog._request_save()
    assert not dialog.save_button.isEnabled()
    dialog.finish_save(requested[-1])
    assert dialog.save_button.isEnabled()

    dialog._request_save()

    assert len(requested) == 2
    assert not dialog.save_button.isEnabled()
    dialog.finish_save(requested[-1])
    dialog.close()


def test_dynamic_model_refresh_preserves_deepseek_and_never_selects_qwen(
    application,
):
    clients = []

    def factory(settings):
        client = FakeModelClient(["qwen2.5-coder:7b"])
        clients.append(client)
        return client

    dialog = SettingsDialog(DEFAULT_SETTINGS, model_client_factory=factory)

    dialog.refresh_models()
    wait_until(application, lambda: dialog._model_thread is None)

    assert dialog.model_combo.currentText() == "deepseek-r1:8b"
    assert [dialog.model_combo.itemText(index) for index in range(dialog.model_combo.count())] == [
        "deepseek-r1:8b",
        "qwen2.5-coder:7b",
    ]
    assert clients[0].closed is True
    dialog.close()


def test_settings_save_rechecks_hotkeys_and_applies_values(application):
    managers = []
    saved = []
    autostart_values = []

    def manager_factory(window_id):
        manager = FakeHotkeyManager()
        managers.append(manager)
        return manager

    window = MainWindow(
        DEFAULT_SETTINGS,
        hotkey_manager_factory=manager_factory,
        settings_saver=saved.append,
        autostart_configurer=lambda enabled: autostart_values.append(enabled) or True,
    )
    assert window.start_hotkeys() is True
    dialog = SettingsDialog(DEFAULT_SETTINGS, parent=window)
    candidate = replace(
        DEFAULT_SETTINGS,
        translation_hotkey="Ctrl+F8",
        ocr_hotkey="Ctrl+F9",
        max_chars_per_chunk=4096,
        start_with_windows=False,
    )

    window._apply_settings(dialog, candidate)

    assert saved == [candidate]
    assert autostart_values == [False]
    assert window._settings == candidate
    assert window._controller._settings == candidate
    assert managers[0].unregistered is True
    assert [spec.display_name for spec in managers[1].registered] == [
        "Ctrl+F8",
        "Ctrl+F9",
    ]
    window.close()


def test_conflicting_new_hotkey_is_not_saved_and_old_hotkeys_are_restored(
    application,
):
    managers = []
    saved = []

    def manager_factory(window_id):
        rejected = "Ctrl+F8" if len(managers) == 1 else None
        manager = FakeHotkeyManager(rejected_hotkey=rejected)
        managers.append(manager)
        return manager

    window = MainWindow(
        DEFAULT_SETTINGS,
        hotkey_manager_factory=manager_factory,
        settings_saver=saved.append,
    )
    assert window.start_hotkeys() is True
    dialog = SettingsDialog(DEFAULT_SETTINGS, parent=window)
    candidate = replace(
        DEFAULT_SETTINGS,
        translation_hotkey="Ctrl+F8",
        ocr_hotkey="Ctrl+F9",
    )

    window._apply_settings(dialog, candidate)

    assert saved == []
    assert window._settings == DEFAULT_SETTINGS
    assert len(managers) == 3
    assert managers[1].unregistered is True
    assert [spec.display_name for spec in managers[2].registered] == [
        "Alt+T",
        "Alt+Q",
    ]
    assert "already in use" in dialog.feedback_label.text()
    window.close()


def test_save_checks_windows_hotkeys_even_when_startup_registration_failed(
    application,
):
    managers = []
    saved = []

    def manager_factory(window_id):
        manager = FakeHotkeyManager(rejected_hotkey="Ctrl+F8")
        managers.append(manager)
        return manager

    window = MainWindow(
        DEFAULT_SETTINGS,
        hotkey_manager_factory=manager_factory,
        settings_saver=saved.append,
    )
    dialog = SettingsDialog(DEFAULT_SETTINGS, parent=window)
    candidate = replace(
        DEFAULT_SETTINGS,
        translation_hotkey="Ctrl+F8",
        ocr_hotkey="Ctrl+F9",
    )

    window._apply_settings(dialog, candidate)

    assert saved == []
    assert window._hotkey_manager is None
    assert len(managers) == 1
    assert managers[0].unregistered is True
    assert "already in use" in dialog.feedback_label.text()
    window.close()


def test_same_settings_dialog_can_save_change_and_then_restore_without_restart(
    application,
):
    managers = []
    saved = []

    def manager_factory(window_id):
        manager = FakeHotkeyManager()
        managers.append(manager)
        return manager

    window = MainWindow(
        DEFAULT_SETTINGS,
        hotkey_manager_factory=manager_factory,
        settings_saver=saved.append,
    )
    assert window.start_hotkeys() is True
    dialog = SettingsDialog(DEFAULT_SETTINGS, parent=window)
    dialog.save_requested.connect(
        lambda settings: window._apply_settings(dialog, settings)
    )

    dialog.translation_hotkey_edit.setText("Ctrl+F8")
    dialog.ocr_hotkey_edit.setText("Ctrl+F9")
    dialog._request_save()
    assert dialog.save_button.isEnabled()

    dialog.translation_hotkey_edit.setText("Alt+T")
    dialog.ocr_hotkey_edit.setText("Alt+Q")
    dialog._request_save()

    assert [settings.translation_hotkey for settings in saved] == [
        "Ctrl+F8",
        "Alt+T",
    ]
    assert window._settings == DEFAULT_SETTINGS
    assert dialog.save_button.isEnabled()
    assert [spec.display_name for spec in managers[-1].registered] == [
        "Alt+T",
        "Alt+Q",
    ]
    window.close()
