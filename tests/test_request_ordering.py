import os
import logging
import threading
import time
from dataclasses import replace

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from locallens.config import DEFAULT_SETTINGS
from locallens.controller import TranslationController
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
    raise AssertionError("timed out while waiting for request ordering")


def dispose_controller(application, controller):
    controller.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    application.processEvents()


class ControlledClient:
    def __init__(self):
        self.started = {"A": threading.Event(), "B": threading.Event()}
        self.release = {"A": threading.Event(), "B": threading.Event()}
        self.calls = []
        self.close_calls = 0
        self._lock = threading.Lock()

    def translate(self, text):
        with self._lock:
            self.calls.append(text)
        self.started[text].set()
        if not self.release[text].wait(timeout=1.5):
            raise RuntimeError(f"request {text} was not released")
        return f"result-{text}"

    def close(self):
        with self._lock:
            self.close_calls += 1


def test_latest_request_wins_when_b_finishes_before_a(application):
    client = ControlledClient()
    controller = TranslationController(
        DEFAULT_SETTINGS, client_factory=lambda settings: client
    )
    displayed = []
    failures = []
    finished = []
    controller.translation_succeeded.connect(
        lambda request_id, text: displayed.append((request_id, text))
    )
    controller.translation_failed.connect(
        lambda request_id, message: failures.append((request_id, message))
    )
    controller.request_finished.connect(finished.append)

    request_a = controller.start_translation("A")
    wait_until(application, client.started["A"].is_set)
    request_b = controller.start_translation("B")
    wait_until(application, client.started["B"].is_set)

    assert request_a == 1
    assert request_b == 2
    assert controller.active_request_id == request_b
    assert controller.running_request_ids == (request_a, request_b)

    client.release["B"].set()
    wait_until(application, lambda: request_b in finished)

    assert displayed == [(request_b, "result-B")]
    assert failures == []

    client.release["A"].set()
    wait_until(application, lambda: not controller.has_running_requests)

    assert displayed == [(request_b, "result-B")]
    assert set(finished) == {request_a, request_b}
    assert client.close_calls == 2
    dispose_controller(application, controller)


def test_stale_failure_is_ignored_after_newer_success(application):
    class OlderFailureClient(ControlledClient):
        def translate(self, text):
            result = super().translate(text)
            if text == "A":
                raise RuntimeError("stale private failure")
            return result

    client = OlderFailureClient()
    controller = TranslationController(
        DEFAULT_SETTINGS, client_factory=lambda settings: client
    )
    displayed = []
    failures = []
    controller.translation_succeeded.connect(
        lambda request_id, text: displayed.append((request_id, text))
    )
    controller.translation_failed.connect(
        lambda request_id, message: failures.append((request_id, message))
    )

    request_a = controller.start_translation("A")
    wait_until(application, client.started["A"].is_set)
    request_b = controller.start_translation("B")
    wait_until(application, client.started["B"].is_set)

    client.release["B"].set()
    wait_until(application, lambda: bool(displayed))
    client.release["A"].set()
    wait_until(application, lambda: not controller.has_running_requests)

    assert displayed == [(request_b, "result-B")]
    assert failures == []
    assert request_a != controller.active_request_id
    dispose_controller(application, controller)


def test_mixed_chinese_english_runs_through_client(application):
    class MixedClient:
        def __init__(self):
            self.calls = []

        def translate(self, text):
            self.calls.append(text)
            return "Please open Windows settings"

        def close(self):
            pass

    client = MixedClient()
    controller = TranslationController(
        DEFAULT_SETTINGS,
        client_factory=lambda settings: client,
    )
    succeeded = []
    finished = []
    controller.translation_succeeded.connect(
        lambda request_id, text: succeeded.append((request_id, text))
    )
    controller.request_finished.connect(finished.append)

    source_text = "请打开 Windows settings"
    request_id = controller.start_translation(source_text)
    wait_until(application, lambda: request_id in finished)

    assert client.calls == [source_text]
    assert succeeded == [(request_id, "Please open Windows settings")]
    assert finished == [request_id]
    assert not controller.has_running_requests
    dispose_controller(application, controller)


def test_long_text_chunks_translate_in_order_and_preserve_structure(application):
    class UppercaseClient:
        def __init__(self):
            self.calls = []
            self.close_calls = 0

        def translate(self, text):
            self.calls.append(text)
            return text.upper()

        def close(self):
            self.close_calls += 1

    source_text = "first sentence. second sentence.\n\nthird paragraph."
    settings = replace(DEFAULT_SETTINGS, max_chars_per_chunk=20)
    client = UppercaseClient()
    controller = TranslationController(
        settings,
        client_factory=lambda current_settings: client,
    )
    displayed = []
    controller.translation_succeeded.connect(
        lambda request_id, text: displayed.append((request_id, text))
    )

    request_id = controller.start_translation(source_text)
    wait_until(application, lambda: not controller.has_running_requests)

    assert displayed == [(request_id, source_text.upper())]
    assert client.calls == [
        "first sentence.",
        "second sentence.",
        "third paragraph.",
    ]
    assert client.close_calls == 1
    dispose_controller(application, controller)


def test_translation_performance_log_omits_source_and_result(application, tmp_path):
    log_path = tmp_path / "locallens.log"
    configure_logging(path=log_path)
    private_source = "PRIVATE_SOURCE_MUST_NOT_BE_LOGGED"
    private_result = "PRIVATE_RESULT_MUST_NOT_BE_LOGGED"

    class PrivateClient:
        def translate(self, text):
            assert text == private_source
            return private_result

        def close(self):
            pass

    controller = TranslationController(
        DEFAULT_SETTINGS,
        client_factory=lambda settings: PrivateClient(),
    )
    controller.start_translation(private_source)
    wait_until(application, lambda: not controller.has_running_requests)
    for handler in logging.getLogger("locallens").handlers:
        handler.flush()
    content = log_path.read_text(encoding="utf-8")

    assert "translation_started" in content
    assert "translation_completed" in content
    assert "duration_ms=" in content
    assert private_source not in content
    assert private_result not in content
    dispose_controller(application, controller)
    shutdown_logging()
