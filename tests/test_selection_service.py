import os
import time
import ctypes
import logging

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from locallens.services.clipboard_service import ClipboardSnapshot
from locallens.logging_config import configure_logging, shutdown_logging
from locallens.services.selection_service import (
    NO_TEXT_MESSAGE,
    SelectionService,
    SelectionTimings,
    _Input,
)


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
    raise AssertionError("timed out while waiting for selection service")


class FakeClipboardService:
    def __init__(self, sequence=10, text="original"):
        self.sequence = sequence
        self.text = text
        self.restore_result = True
        self.restore_calls = []
        self.change_during_read = False

    def capture_snapshot(self):
        return ClipboardSnapshot(text=self.text, sequence_number=self.sequence)

    def sequence_number(self):
        return self.sequence

    def read_text(self):
        text = self.text
        if self.change_during_read:
            self.sequence += 1
            self.text = "newer user copy"
        return text

    def restore_snapshot(self, snapshot, *, expected_sequence_number):
        self.restore_calls.append(expected_sequence_number)
        if self.sequence != expected_sequence_number or not self.restore_result:
            return False
        self.text = snapshot.text
        self.sequence += 1
        return True


class FakeInputBackend:
    def __init__(
        self,
        *,
        held_checks=0,
        restore_result=True,
        send_result=True,
        on_send=None,
    ):
        self.held_checks = held_checks
        self.restore_result = restore_result
        self.send_result = send_result
        self.on_send = on_send
        self.target = object()
        self.capture_calls = 0
        self.restore_calls = []
        self.send_calls = 0

    def is_key_down(self, virtual_key):
        if self.held_checks > 0:
            self.held_checks -= 1
            return True
        return False

    def capture_target(self):
        self.capture_calls += 1
        return self.target

    def restore_target(self, target):
        self.restore_calls.append(target)
        return self.restore_result

    def send_ctrl_c(self):
        self.send_calls += 1
        if self.on_send is not None:
            self.on_send()
        return self.send_result


def fast_timings(clipboard_timeout_ms=80):
    return SelectionTimings(
        modifier_release_timeout_ms=100,
        modifier_poll_interval_ms=5,
        post_release_delay_ms=5,
        clipboard_timeout_ms=clipboard_timeout_ms,
        clipboard_poll_interval_ms=5,
    )


def test_win32_input_structure_has_native_union_size():
    expected_size = 40 if ctypes.sizeof(ctypes.c_void_p) == 8 else 28

    assert ctypes.sizeof(_Input) == expected_size


def test_waits_for_all_hotkey_keys_then_restores_and_emits_text(application):
    clipboard = FakeClipboardService()

    def copy_selected_text():
        clipboard.sequence += 1
        clipboard.text = "selected text"

    input_backend = FakeInputBackend(held_checks=2, on_send=copy_selected_text)
    service = SelectionService(
        clipboard,  # type: ignore[arg-type]
        input_backend=input_backend,
        timings=fast_timings(),
    )
    results = []
    heartbeat = []
    service.text_ready.connect(lambda text, restored: results.append((text, restored)))

    assert service.capture_selected_text((0x12, 0x54)) is True
    QTimer.singleShot(0, lambda: heartbeat.append(True))
    assert input_backend.send_calls == 0
    wait_until(application, lambda: bool(results))

    assert heartbeat == [True]
    assert input_backend.capture_calls == 1
    assert input_backend.restore_calls == [input_backend.target]
    assert input_backend.send_calls == 1
    assert results == [("selected text", True)]
    assert clipboard.text == "original"
    assert clipboard.restore_calls == [11]


def test_no_clipboard_change_reports_fallback_without_restore(application):
    clipboard = FakeClipboardService()
    input_backend = FakeInputBackend()
    service = SelectionService(
        clipboard,  # type: ignore[arg-type]
        input_backend=input_backend,
        timings=fast_timings(clipboard_timeout_ms=30),
    )
    failures = []
    service.failed.connect(failures.append)

    service.capture_selected_text(())
    wait_until(application, lambda: bool(failures))

    assert failures == [NO_TEXT_MESSAGE]
    assert clipboard.restore_calls == []


def test_non_text_copy_is_restored_safely_before_failure(application):
    clipboard = FakeClipboardService()

    def copy_non_text():
        clipboard.sequence += 1
        clipboard.text = None

    service = SelectionService(
        clipboard,  # type: ignore[arg-type]
        input_backend=FakeInputBackend(on_send=copy_non_text),
        timings=fast_timings(clipboard_timeout_ms=30),
    )
    failures = []
    service.failed.connect(failures.append)

    service.capture_selected_text(())
    wait_until(application, lambda: bool(failures))

    assert failures == [NO_TEXT_MESSAGE]
    assert clipboard.restore_calls == [11]
    assert clipboard.text == "original"


def test_send_input_failure_is_reported(application):
    clipboard = FakeClipboardService()
    service = SelectionService(
        clipboard,  # type: ignore[arg-type]
        input_backend=FakeInputBackend(send_result=False),
        timings=fast_timings(),
    )
    failures = []
    service.failed.connect(failures.append)

    service.capture_selected_text(())
    wait_until(application, lambda: bool(failures))

    assert "Could not send Ctrl+C" in failures[0]


def test_lost_target_focus_is_reported_without_sending_copy(application):
    clipboard = FakeClipboardService()
    input_backend = FakeInputBackend(restore_result=False)
    service = SelectionService(
        clipboard,  # type: ignore[arg-type]
        input_backend=input_backend,
        timings=fast_timings(),
    )
    failures = []
    service.failed.connect(failures.append)

    service.capture_selected_text(())
    wait_until(application, lambda: bool(failures))

    assert "lost keyboard focus" in failures[0]
    assert input_backend.send_calls == 0


def test_second_clipboard_change_is_not_overwritten(application):
    clipboard = FakeClipboardService()

    def copy_selected_text():
        clipboard.sequence += 1
        clipboard.text = "selected text"
        clipboard.change_during_read = True

    service = SelectionService(
        clipboard,  # type: ignore[arg-type]
        input_backend=FakeInputBackend(on_send=copy_selected_text),
        timings=fast_timings(),
    )
    failures = []
    results = []
    service.failed.connect(failures.append)
    service.text_ready.connect(lambda text, restored: results.append((text, restored)))

    service.capture_selected_text(())
    wait_until(application, lambda: bool(failures))

    assert results == []
    assert "changed again" in failures[0]
    assert clipboard.text == "newer user copy"
    assert clipboard.restore_calls == []


def test_failed_guard_returns_text_but_preserves_newer_clipboard(application):
    clipboard = FakeClipboardService()
    clipboard.restore_result = False

    def copy_selected_text():
        clipboard.sequence += 1
        clipboard.text = "selected text"

    service = SelectionService(
        clipboard,  # type: ignore[arg-type]
        input_backend=FakeInputBackend(on_send=copy_selected_text),
        timings=fast_timings(),
    )
    results = []
    service.text_ready.connect(lambda text, restored: results.append((text, restored)))

    service.capture_selected_text(())
    wait_until(application, lambda: bool(results))

    assert results == [("selected text", False)]
    assert clipboard.text == "selected text"


def test_clipboard_log_records_result_without_selected_text(application, tmp_path):
    log_path = tmp_path / "locallens.log"
    configure_logging(path=log_path)
    clipboard = FakeClipboardService()
    private_text = "PRIVATE_SELECTED_TEXT_DO_NOT_LOG"

    def copy_selected_text():
        clipboard.sequence += 1
        clipboard.text = private_text

    service = SelectionService(
        clipboard,  # type: ignore[arg-type]
        input_backend=FakeInputBackend(on_send=copy_selected_text),
        timings=fast_timings(),
    )
    results = []
    service.text_ready.connect(lambda text, restored: results.append((text, restored)))

    service.capture_selected_text(())
    wait_until(application, lambda: bool(results))
    for handler in logging.getLogger("locallens").handlers:
        handler.flush()
    content = log_path.read_text(encoding="utf-8")

    assert "clipboard_acquisition_started" in content
    assert "clipboard_acquisition_succeeded" in content
    assert "duration_ms=" in content
    assert private_text not in content
    shutdown_logging()
