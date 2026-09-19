"""Concurrent translation execution with latest-request-wins ordering."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import Protocol

from PySide6.QtCore import QObject, QThread, Signal, Slot

from locallens.config import Settings
from locallens.services.ollama_client import OllamaClient, OllamaError
from locallens.text_chunking import iter_translatable_parts
from locallens.translation_policy import is_mixed_chinese_english


logger = logging.getLogger("locallens.translation")


class TranslationClient(Protocol):
    def translate(self, text: str) -> str: ...

    def close(self) -> None: ...


ClientFactory = Callable[[Settings], TranslationClient]


class TranslationWorker(QObject):
    """Execute one independent translation request on its own thread."""

    succeeded = Signal(int, str)
    failed = Signal(int, str)
    completed = Signal(int)

    def __init__(
        self,
        request_id: int,
        settings: Settings,
        source_text: str,
        client_factory: ClientFactory,
    ) -> None:
        super().__init__()
        self._request_id = request_id
        self._settings = settings
        self._source_text = source_text
        self._client_factory = client_factory

    @Slot()
    def run(self) -> None:
        client: TranslationClient | None = None
        try:
            client = self._client_factory(self._settings)
            translated_parts: list[str] = []
            for leading, content, trailing in iter_translatable_parts(
                self._source_text,
                self._settings.max_chars_per_chunk,
            ):
                if content:
                    translated = client.translate(content)
                    translated_parts.extend((leading, translated, trailing))
                else:
                    translated_parts.append(leading)
            translation = "".join(translated_parts)
        except OllamaError as exc:
            logger.warning(
                "translation_failed request_id=%d exception_type=%s",
                self._request_id,
                type(exc).__name__,
            )
            self.failed.emit(self._request_id, str(exc))
        except Exception as exc:
            logger.error(
                "translation_failed request_id=%d exception_type=%s",
                self._request_id,
                type(exc).__name__,
            )
            self.failed.emit(self._request_id, "Translation failed unexpectedly.")
        else:
            self.succeeded.emit(self._request_id, translation)
        finally:
            if client is not None:
                try:
                    client.close()
                except Exception:
                    pass
            self.completed.emit(self._request_id)


class TranslationController(QObject):
    """Run independent requests and publish results from only the newest one."""

    translation_succeeded = Signal(int, str)
    translation_bypassed = Signal(int, str)
    translation_failed = Signal(int, str)
    request_finished = Signal(int)

    def __init__(
        self,
        settings: Settings,
        *,
        client_factory: ClientFactory = OllamaClient,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._client_factory = client_factory
        self._next_request_id = 1
        self._active_request_id: int | None = None
        self._requests: dict[int, tuple[QThread, TranslationWorker]] = {}
        self._request_started_at: dict[int, float] = {}

    @property
    def active_request_id(self) -> int | None:
        return self._active_request_id

    @property
    def running_request_ids(self) -> tuple[int, ...]:
        return tuple(self._requests)

    @property
    def has_running_requests(self) -> bool:
        return bool(self._requests)

    def update_settings(self, settings: Settings) -> None:
        """Apply settings to future requests without disturbing active work."""

        if self.has_running_requests:
            raise RuntimeError("Cannot update settings during a translation request.")
        self._settings = settings

    def invalidate_active_request(self) -> None:
        """Keep workers alive but prevent an older result from reaching the UI."""

        self._active_request_id = None

    def start_translation(self, source_text: str) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._active_request_id = request_id
        self._request_started_at[request_id] = time.monotonic()
        logger.info("translation_started request_id=%d thinking=true", request_id)

        if is_mixed_chinese_english(source_text):
            logger.info(
                "translation_bypassed request_id=%d reason=mixed_language",
                request_id,
            )
            self.translation_bypassed.emit(request_id, source_text)
            self._log_request_completed(request_id, "bypassed")
            self._request_started_at.pop(request_id, None)
            self.request_finished.emit(request_id)
            return request_id

        thread = QThread(self)
        worker = TranslationWorker(
            request_id,
            self._settings,
            source_text,
            self._client_factory,
        )
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.succeeded.connect(self._on_succeeded)
        worker.failed.connect(self._on_failed)
        worker.completed.connect(worker.deleteLater)
        worker.completed.connect(thread.quit)
        thread.finished.connect(
            lambda request_id=request_id: self._on_thread_finished(request_id)
        )
        thread.finished.connect(thread.deleteLater)

        self._requests[request_id] = (thread, worker)
        thread.start()
        return request_id

    @Slot(int, str)
    def _on_succeeded(self, request_id: int, translation: str) -> None:
        self._log_request_completed(request_id, "success")
        if request_id == self._active_request_id:
            self.translation_succeeded.emit(request_id, translation)

    @Slot(int, str)
    def _on_failed(self, request_id: int, message: str) -> None:
        self._log_request_completed(request_id, "failed")
        if request_id == self._active_request_id:
            self.translation_failed.emit(request_id, message)

    def _log_request_completed(self, request_id: int, status: str) -> None:
        started_at = self._request_started_at.get(request_id)
        if started_at is None:
            return
        logger.info(
            "translation_completed request_id=%d thinking=true status=%s duration_ms=%d",
            request_id,
            status,
            round((time.monotonic() - started_at) * 1000),
        )

    def _on_thread_finished(self, request_id: int) -> None:
        request = self._requests.get(request_id)
        if request is not None:
            thread, _worker = request
            # ``finished`` is emitted just before QThread completes its final
            # native cleanup. Waiting here prevents a controller teardown from
            # destroying a thread object during that narrow interval.
            thread.wait()
            self._requests.pop(request_id, None)
        self._request_started_at.pop(request_id, None)
        self.request_finished.emit(request_id)
