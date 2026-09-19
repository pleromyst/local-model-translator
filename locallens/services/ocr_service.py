"""Reusable RapidOCR engine and serialized background recognition."""

from __future__ import annotations

import logging
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Protocol

import numpy as np
from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtGui import QImage


NO_TEXT_MESSAGE = "No text was detected in this area."
INVALID_CAPTURE_MESSAGE = "Screen capture was unavailable for this content."
OCR_TEXT_SCORE_THRESHOLD = 0.65
OCR_BOX_SCORE_THRESHOLD = 0.60
logger = logging.getLogger("locallens.ocr")


class OCRError(RuntimeError):
    """Base class for safe OCR failures that can be shown in the UI."""


class OCRInitializationError(OCRError):
    """RapidOCR or its ONNX models could not be initialized."""


class OCRRecognitionError(OCRError):
    """A supplied image could not be recognized."""


class OCREngine(Protocol):
    def __call__(self, image: np.ndarray) -> Any: ...


EngineFactory = Callable[[], OCREngine]
ServiceFactory = Callable[[], "OCRService"]


@dataclass(frozen=True)
class _TextRegion:
    text: str
    left: float
    top: float
    right: float
    bottom: float

    @property
    def center_y(self) -> float:
        return (self.top + self.bottom) / 2

    @property
    def height(self) -> float:
        return max(self.bottom - self.top, 1.0)


def _create_rapidocr_engine() -> OCREngine:
    from rapidocr import RapidOCR

    return RapidOCR(
        params={
            "Global.log_level": "error",
            "Global.text_score": OCR_TEXT_SCORE_THRESHOLD,
            "Det.box_thresh": OCR_BOX_SCORE_THRESHOLD,
        }
    )


def qimage_to_bgr(image: QImage) -> np.ndarray:
    """Copy a QImage into the contiguous BGR array expected by RapidOCR."""

    if not isinstance(image, QImage) or image.isNull():
        raise OCRRecognitionError("The captured image is empty.")

    rgb_image = image.convertToFormat(QImage.Format.Format_RGB888)
    width = rgb_image.width()
    height = rgb_image.height()
    bytes_per_line = rgb_image.bytesPerLine()
    raw = bytes(rgb_image.constBits())
    if len(raw) < bytes_per_line * height:
        raise OCRRecognitionError("The captured image could not be read.")

    rows = np.frombuffer(
        raw,
        dtype=np.uint8,
        count=bytes_per_line * height,
    ).reshape(height, bytes_per_line)
    rgb = rows[:, : width * 3].reshape(height, width, 3)
    return np.ascontiguousarray(rgb[:, :, ::-1])


def _capture_is_unavailable(image: np.ndarray) -> bool:
    """Detect the near-solid black/white frames returned by blocked capture APIs."""

    if image.size == 0:
        return True
    dark_pixels = np.all(image <= 5, axis=2)
    light_pixels = np.all(image >= 250, axis=2)
    return bool(dark_pixels.mean() >= 0.999 or light_pixels.mean() >= 0.999)


def _is_cjk(character: str) -> bool:
    codepoint = ord(character)
    return (
        0x3400 <= codepoint <= 0x4DBF
        or 0x4E00 <= codepoint <= 0x9FFF
        or 0xF900 <= codepoint <= 0xFAFF
        or 0x3040 <= codepoint <= 0x30FF
        or 0xAC00 <= codepoint <= 0xD7AF
    )


def _needs_space(previous: str, current: str) -> bool:
    if not previous or not current:
        return False
    previous_character = previous[-1]
    current_character = current[0]
    if _is_cjk(previous_character) or _is_cjk(current_character):
        return False
    if current_character in ",.!?:;%)]}\u3001\u3002\uff01\uff0c\uff1a\uff1b\uff1f\uff09\u3011\u300b":
        return False
    if previous_character in "([{\uff08\u3010\u300a":
        return False
    if unicodedata.category(current_character).startswith("M"):
        return False
    return True


def _join_text_parts(parts: list[str]) -> str:
    result = ""
    for part in parts:
        if result and _needs_space(result, part):
            result += " "
        result += part
    return result


def _join_line(regions: list[_TextRegion]) -> str:
    ordered = sorted(regions, key=lambda region: region.left)
    return _join_text_parts([region.text for region in ordered])


def _filter_by_confidence(
    texts: Any,
    boxes: Any,
    scores: Any,
) -> tuple[tuple[Any, ...], Any, int, int]:
    """Discard uncertain recognition while tolerating engines without scores."""

    raw_texts = tuple(texts) if texts is not None else ()
    total_count = len(raw_texts)
    if scores is None:
        return raw_texts, boxes, total_count, total_count

    raw_scores = tuple(scores)
    if len(raw_scores) != total_count:
        return raw_texts, boxes, total_count, total_count

    accepted_indices: list[int] = []
    for index, score in enumerate(raw_scores):
        try:
            numeric_score = float(score)
        except (TypeError, ValueError):
            continue
        if np.isfinite(numeric_score) and numeric_score >= OCR_TEXT_SCORE_THRESHOLD:
            accepted_indices.append(index)

    accepted_texts = tuple(raw_texts[index] for index in accepted_indices)
    if boxes is not None and len(boxes) == total_count:
        accepted_boxes: Any = tuple(boxes[index] for index in accepted_indices)
    else:
        accepted_boxes = boxes
    return accepted_texts, accepted_boxes, total_count, len(accepted_indices)


def _reconstruct_text(texts: Any, boxes: Any) -> str:
    """Rebuild natural text lines from RapidOCR's individual text boxes."""

    cleaned_texts = [str(text).strip() for text in texts or () if str(text).strip()]
    if not cleaned_texts:
        return ""
    if boxes is None or len(boxes) != len(texts):
        return _join_text_parts(cleaned_texts)

    regions: list[_TextRegion] = []
    for text, box in zip(texts, boxes, strict=True):
        cleaned = str(text).strip()
        if not cleaned:
            continue
        coordinates = np.asarray(box, dtype=float)
        if coordinates.ndim != 2 or coordinates.shape[1] != 2:
            return _join_text_parts(cleaned_texts)
        regions.append(
            _TextRegion(
                text=cleaned,
                left=float(coordinates[:, 0].min()),
                top=float(coordinates[:, 1].min()),
                right=float(coordinates[:, 0].max()),
                bottom=float(coordinates[:, 1].max()),
            )
        )

    lines: list[list[_TextRegion]] = []
    for region in sorted(regions, key=lambda item: (item.center_y, item.left)):
        best_line: list[_TextRegion] | None = None
        best_distance = float("inf")
        for line in lines:
            line_center = sum(item.center_y for item in line) / len(line)
            line_height = sum(item.height for item in line) / len(line)
            distance = abs(region.center_y - line_center)
            if distance <= 0.5 * max(region.height, line_height) and distance < best_distance:
                best_line = line
                best_distance = distance
        if best_line is None:
            lines.append([region])
        else:
            best_line.append(region)

    lines.sort(key=lambda line: sum(item.center_y for item in line) / len(line))
    return _join_text_parts([_join_line(line) for line in lines])


class OCRService:
    """Synchronous OCR facade that constructs its engine exactly once."""

    def __init__(self, *, engine_factory: EngineFactory = _create_rapidocr_engine):
        started_at = time.monotonic()
        try:
            self._engine = engine_factory()
        except Exception as exc:
            logger.error(
                "ocr_initialization_failed duration_ms=%d exception_type=%s",
                round((time.monotonic() - started_at) * 1000),
                type(exc).__name__,
            )
            raise OCRInitializationError(
                "The OCR engine could not be initialized."
            ) from exc
        logger.info(
            "ocr_initialized duration_ms=%d",
            round((time.monotonic() - started_at) * 1000),
        )

    def recognize(self, image: QImage) -> str:
        started_at = time.monotonic()
        try:
            array = qimage_to_bgr(image)
        except OCRRecognitionError:
            logger.warning(
                "ocr_completed status=invalid_image duration_ms=%d",
                round((time.monotonic() - started_at) * 1000),
            )
            raise
        if _capture_is_unavailable(array):
            logger.warning(
                "ocr_completed status=invalid_capture duration_ms=%d",
                round((time.monotonic() - started_at) * 1000),
            )
            raise OCRRecognitionError(INVALID_CAPTURE_MESSAGE)
        try:
            result = self._engine(array)
        except Exception as exc:
            logger.error(
                "ocr_completed status=failed duration_ms=%d exception_type=%s",
                round((time.monotonic() - started_at) * 1000),
                type(exc).__name__,
            )
            raise OCRRecognitionError("OCR failed unexpectedly.") from exc
        filtered_texts, filtered_boxes, region_count, accepted_count = (
            _filter_by_confidence(
                getattr(result, "txts", None),
                getattr(result, "boxes", None),
                getattr(result, "scores", None),
            )
        )
        text = _reconstruct_text(
            filtered_texts,
            filtered_boxes,
        )
        logger.info(
            "ocr_completed status=%s duration_ms=%d regions=%d accepted=%d",
            "success" if text.strip() else "no_text",
            round((time.monotonic() - started_at) * 1000),
            region_count,
            accepted_count,
        )
        return text


class _OCRWorker(QObject):
    initialized = Signal()
    initialization_failed = Signal(str)
    succeeded = Signal(int, str)
    failed = Signal(int, str)

    def __init__(self, service_factory: ServiceFactory) -> None:
        super().__init__()
        self._service_factory = service_factory
        self._service: OCRService | None = None
        self._initialization_error: str | None = None

    @Slot()
    def initialize(self) -> None:
        if self._service is not None or self._initialization_error is not None:
            return
        try:
            self._service = self._service_factory()
        except OCRError as exc:
            self._initialization_error = str(exc)
            self.initialization_failed.emit(str(exc))
        except Exception:
            self._initialization_error = "The OCR engine could not be initialized."
            logger.error("ocr_worker_initialization_failed exception_type=unexpected")
            self.initialization_failed.emit(self._initialization_error)
        else:
            self.initialized.emit()

    @Slot(int, object)
    def recognize(self, request_id: int, image: QImage) -> None:
        if self._service is None and self._initialization_error is None:
            self.initialize()
        if self._service is None:
            self.failed.emit(
                request_id,
                self._initialization_error
                or "The OCR engine could not be initialized.",
            )
            return
        try:
            text = self._service.recognize(image)
        except OCRError as exc:
            self.failed.emit(request_id, str(exc))
        except Exception as exc:
            logger.error(
                "ocr_worker_failed exception_type=%s",
                type(exc).__name__,
            )
            self.failed.emit(request_id, "OCR failed unexpectedly.")
        else:
            self.succeeded.emit(request_id, text)


class OCRController(QObject):
    """Keep one OCR engine on a worker thread and serialize image requests."""

    _recognize_requested = Signal(int, object)

    initialized = Signal()
    initialization_failed = Signal(str)
    text_ready = Signal(int, str)
    no_text = Signal(int)
    failed = Signal(int, str)

    def __init__(
        self,
        *,
        service_factory: ServiceFactory = OCRService,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._next_request_id = 1
        self._active_request_id: int | None = None
        self._thread = QThread(self)
        self._worker = _OCRWorker(service_factory)
        self._worker.moveToThread(self._thread)

        self._thread.started.connect(self._worker.initialize)
        self._recognize_requested.connect(self._worker.recognize)
        self._worker.initialized.connect(self.initialized)
        self._worker.initialization_failed.connect(self.initialization_failed)
        self._worker.succeeded.connect(self._on_succeeded)
        self._worker.failed.connect(self._on_failed)
        self._thread.finished.connect(self._worker.deleteLater)
        self._thread.start()

    @property
    def active_request_id(self) -> int | None:
        return self._active_request_id

    @property
    def is_running(self) -> bool:
        return self._thread.isRunning()

    def recognize(self, image: QImage) -> int:
        request_id = self._next_request_id
        self._next_request_id += 1
        self._active_request_id = request_id
        self._recognize_requested.emit(request_id, image.copy())
        return request_id

    def invalidate_active_request(self) -> None:
        """Ignore a queued result after a newer non-OCR workflow takes over."""

        self._active_request_id = None

    @Slot(int, str)
    def _on_succeeded(self, request_id: int, text: str) -> None:
        if request_id != self._active_request_id:
            return
        if text.strip():
            self.text_ready.emit(request_id, text)
        else:
            self.no_text.emit(request_id)

    @Slot(int, str)
    def _on_failed(self, request_id: int, message: str) -> None:
        if request_id == self._active_request_id:
            self.failed.emit(request_id, message)

    def shutdown(self, timeout_ms: int = 5000) -> bool:
        if not self._thread.isRunning():
            return True
        self._thread.quit()
        return self._thread.wait(timeout_ms)
