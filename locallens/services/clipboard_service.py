"""Best-effort MIME clipboard snapshots with guarded restoration."""

from __future__ import annotations

import ctypes
import sys
from collections.abc import Callable
from dataclasses import dataclass, field, replace
from typing import Protocol

from PySide6.QtCore import QByteArray, QMimeData, QUrl
from PySide6.QtGui import QImage, QPixmap
from PySide6.QtWidgets import QApplication


SequenceReader = Callable[[], int | None]


class ClipboardBackend(Protocol):
    def mimeData(self) -> QMimeData | None: ...  # noqa: N802

    def setMimeData(self, source: QMimeData) -> None: ...  # noqa: N802


class ClipboardError(RuntimeError):
    """Base class for clipboard failures that can be shown safely."""


class ClipboardChangedDuringSnapshotError(ClipboardError):
    """The clipboard kept changing while a consistent copy was attempted."""


def _load_windows_sequence_reader() -> Callable[[], int] | None:
    if sys.platform != "win32":
        return None
    try:
        function = ctypes.WinDLL("user32", use_last_error=True).GetClipboardSequenceNumber
    except (AttributeError, OSError):
        return None
    function.argtypes = []
    function.restype = ctypes.c_uint32
    return function


_WINDOWS_SEQUENCE_READER = _load_windows_sequence_reader()


def get_windows_clipboard_sequence_number() -> int | None:
    """Return the Win32 clipboard sequence number, or ``None`` if unavailable."""

    if _WINDOWS_SEQUENCE_READER is None:
        return None
    value = int(_WINDOWS_SEQUENCE_READER())
    return value if value > 0 else None


@dataclass(frozen=True, slots=True)
class ClipboardSnapshot:
    """A detached copy of clipboard data exposed through Qt."""

    raw_mime_data: tuple[tuple[str, bytes], ...] = ()
    text: str | None = None
    html: str | None = None
    urls: tuple[bytes, ...] = ()
    image: QImage | None = field(default=None, repr=False, compare=False)
    unavailable_formats: tuple[str, ...] = ()
    sequence_number: int | None = None

    @property
    def formats(self) -> tuple[str, ...]:
        return tuple(mime_type for mime_type, _ in self.raw_mime_data)

    def to_mime_data(self) -> QMimeData:
        """Create a fresh QMimeData object suitable for QClipboard ownership."""

        mime_data = QMimeData()
        for mime_type, data in self.raw_mime_data:
            mime_data.setData(mime_type, QByteArray(data))
        if self.text is not None:
            mime_data.setText(self.text)
        if self.html is not None:
            mime_data.setHtml(self.html)
        if self.urls:
            mime_data.setUrls(
                [QUrl.fromEncoded(QByteArray(encoded_url)) for encoded_url in self.urls]
            )
        if self.image is not None:
            mime_data.setImageData(self.image.copy())
        return mime_data


class ClipboardService:
    """Capture and restore the standard clipboard without overwriting newer data."""

    def __init__(
        self,
        clipboard: ClipboardBackend | None = None,
        *,
        sequence_reader: SequenceReader = get_windows_clipboard_sequence_number,
    ) -> None:
        if clipboard is None:
            application = QApplication.instance()
            if application is None:
                raise RuntimeError(
                    "A QApplication must exist before creating ClipboardService."
                )
            clipboard = application.clipboard()
        self._clipboard = clipboard
        self._sequence_reader = sequence_reader

    def sequence_number(self) -> int | None:
        return self._sequence_reader()

    def capture_snapshot(self, *, max_attempts: int = 3) -> ClipboardSnapshot:
        """Capture a consistent snapshot, retrying if the clipboard changes."""

        if max_attempts < 1:
            raise ValueError("max_attempts must be at least 1")

        for _ in range(max_attempts):
            sequence_before = self.sequence_number()
            mime_data = self._clipboard.mimeData()
            snapshot = self._copy_mime_data(mime_data)
            sequence_after = self.sequence_number()

            if sequence_before is None or sequence_after is None:
                return replace(snapshot, sequence_number=None)
            if sequence_before == sequence_after:
                return replace(snapshot, sequence_number=sequence_after)

        raise ClipboardChangedDuringSnapshotError(
            "The clipboard changed while LocalLens was reading it."
        )

    def read_text(self) -> str | None:
        mime_data = self._clipboard.mimeData()
        if mime_data is None or not mime_data.hasText():
            return None
        text = mime_data.text()
        return text if text else None

    def restore_snapshot(
        self,
        snapshot: ClipboardSnapshot,
        *,
        expected_sequence_number: int | None,
    ) -> bool:
        """Restore only while the clipboard still has the expected sequence.

        Two checks narrow the race window before Qt takes clipboard ownership.
        If sequence tracking is unavailable, restoration is refused.
        """

        if not isinstance(snapshot, ClipboardSnapshot):
            raise TypeError("snapshot must be a ClipboardSnapshot instance")
        if expected_sequence_number is None:
            return False
        if self.sequence_number() != expected_sequence_number:
            return False

        restored_mime_data = snapshot.to_mime_data()
        if self.sequence_number() != expected_sequence_number:
            return False

        self._clipboard.setMimeData(restored_mime_data)
        return True

    @staticmethod
    def _copy_mime_data(mime_data: QMimeData | None) -> ClipboardSnapshot:
        if mime_data is None:
            return ClipboardSnapshot()

        raw_mime_data: list[tuple[str, bytes]] = []
        unavailable_formats: list[str] = []
        for mime_type in mime_data.formats():
            try:
                raw_mime_data.append((mime_type, bytes(mime_data.data(mime_type))))
            except (RuntimeError, TypeError, ValueError):
                unavailable_formats.append(mime_type)

        text: str | None = None
        html: str | None = None
        urls: tuple[bytes, ...] = ()
        image: QImage | None = None

        try:
            if mime_data.hasText():
                text = mime_data.text()
        except (RuntimeError, TypeError, ValueError):
            unavailable_formats.append("text/plain")

        try:
            if mime_data.hasHtml():
                html = mime_data.html()
        except (RuntimeError, TypeError, ValueError):
            unavailable_formats.append("text/html")

        try:
            if mime_data.hasUrls():
                urls = tuple(bytes(url.toEncoded()) for url in mime_data.urls())
        except (RuntimeError, TypeError, ValueError):
            unavailable_formats.append("text/uri-list")

        try:
            if mime_data.hasImage():
                image_data = mime_data.imageData()
                if isinstance(image_data, QImage):
                    image = image_data.copy()
                elif isinstance(image_data, QPixmap):
                    image = image_data.toImage().copy()
                else:
                    unavailable_formats.append("application/x-qt-image")
        except (RuntimeError, TypeError, ValueError):
            unavailable_formats.append("application/x-qt-image")

        return ClipboardSnapshot(
            raw_mime_data=tuple(raw_mime_data),
            text=text,
            html=html,
            urls=urls,
            image=image,
            unavailable_formats=tuple(dict.fromkeys(unavailable_formats)),
        )
