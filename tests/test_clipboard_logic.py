import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QByteArray, QMimeData, QUrl
from PySide6.QtGui import QColor, QImage
from PySide6.QtWidgets import QApplication

from locallens.services.clipboard_service import (
    ClipboardChangedDuringSnapshotError,
    ClipboardService,
)


@pytest.fixture(scope="module")
def application():
    return QApplication.instance() or QApplication([])


class SequenceCounter:
    def __init__(self, value=100):
        self.value = value

    def read(self):
        return self.value


class FakeClipboard:
    def __init__(self, sequence, mime_data=None):
        self.sequence = sequence
        self._mime_data = mime_data or QMimeData()

    def mimeData(self):
        return self._mime_data

    def setMimeData(self, source):
        self._mime_data = source
        self.sequence.value += 1

    def external_set(self, source):
        self._mime_data = source
        self.sequence.value += 1


def text_mime(text):
    mime_data = QMimeData()
    mime_data.setText(text)
    return mime_data


def test_plain_text_snapshot_round_trip(application):
    sequence = SequenceCounter()
    clipboard = FakeClipboard(sequence, text_mime("original text"))
    service = ClipboardService(clipboard, sequence_reader=sequence.read)
    snapshot = service.capture_snapshot()
    clipboard.external_set(text_mime("selected text"))

    restored = service.restore_snapshot(
        snapshot, expected_sequence_number=sequence.value
    )

    assert restored is True
    assert clipboard.mimeData().text() == "original text"
    assert snapshot.text == "original text"
    assert "text/plain" in snapshot.formats


def test_html_and_custom_mime_round_trip(application):
    sequence = SequenceCounter()
    original = QMimeData()
    original.setText("rich text")
    original.setHtml("<p><b>rich</b> text</p>")
    original.setData("application/x-locallens-test", QByteArray(b"custom\x00data"))
    clipboard = FakeClipboard(sequence, original)
    service = ClipboardService(clipboard, sequence_reader=sequence.read)
    snapshot = service.capture_snapshot()
    clipboard.external_set(text_mime("temporary"))

    assert service.restore_snapshot(
        snapshot, expected_sequence_number=sequence.value
    )
    restored = clipboard.mimeData()
    assert restored.text() == "rich text"
    assert restored.html() == "<p><b>rich</b> text</p>"
    assert bytes(restored.data("application/x-locallens-test")) == b"custom\x00data"


def test_image_snapshot_copies_pixel_data(application):
    sequence = SequenceCounter()
    image = QImage(3, 2, QImage.Format.Format_ARGB32)
    image.fill(QColor("#2a7fff"))
    original = QMimeData()
    original.setImageData(image)
    clipboard = FakeClipboard(sequence, original)
    service = ClipboardService(clipboard, sequence_reader=sequence.read)
    snapshot = service.capture_snapshot()
    image.fill(QColor("black"))
    clipboard.external_set(text_mime("temporary"))

    assert service.restore_snapshot(
        snapshot, expected_sequence_number=sequence.value
    )
    restored_image = clipboard.mimeData().imageData()
    assert isinstance(restored_image, QImage)
    assert restored_image.size().toTuple() == (3, 2)
    assert restored_image.pixelColor(0, 0).name() == "#2a7fff"


def test_urls_and_file_list_round_trip(application, tmp_path):
    sequence = SequenceCounter()
    file_path = tmp_path / "example file.txt"
    original_urls = [
        QUrl.fromLocalFile(str(file_path)),
        QUrl("https://example.com/a%20path?q=1"),
    ]
    original = QMimeData()
    original.setUrls(original_urls)
    clipboard = FakeClipboard(sequence, original)
    service = ClipboardService(clipboard, sequence_reader=sequence.read)
    snapshot = service.capture_snapshot()
    clipboard.external_set(text_mime("temporary"))

    assert service.restore_snapshot(
        snapshot, expected_sequence_number=sequence.value
    )
    restored_urls = clipboard.mimeData().urls()
    assert [bytes(url.toEncoded()) for url in restored_urls] == [
        bytes(url.toEncoded()) for url in original_urls
    ]


def test_new_user_clipboard_content_blocks_restore(application):
    sequence = SequenceCounter()
    clipboard = FakeClipboard(sequence, text_mime("original"))
    service = ClipboardService(clipboard, sequence_reader=sequence.read)
    snapshot = service.capture_snapshot()
    clipboard.external_set(text_mime("selected by LocalLens"))
    expected_sequence = sequence.value
    clipboard.external_set(text_mime("new user copy"))

    restored = service.restore_snapshot(
        snapshot, expected_sequence_number=expected_sequence
    )

    assert restored is False
    assert clipboard.mimeData().text() == "new user copy"


def test_restore_is_refused_without_sequence_tracking(application):
    sequence = SequenceCounter()
    clipboard = FakeClipboard(sequence, text_mime("original"))
    service = ClipboardService(clipboard, sequence_reader=lambda: None)
    snapshot = service.capture_snapshot()
    clipboard.external_set(text_mime("new content"))

    restored = service.restore_snapshot(
        snapshot, expected_sequence_number=None
    )

    assert restored is False
    assert clipboard.mimeData().text() == "new content"


def test_restore_is_refused_if_sequence_changes_between_guard_checks(application):
    sequence = SequenceCounter(200)
    clipboard = FakeClipboard(sequence, text_mime("new user content"))
    snapshot_service = ClipboardService(
        clipboard, sequence_reader=sequence.read
    )
    snapshot = snapshot_service.capture_snapshot()
    sequence_values = iter([200, 201])
    guarded_service = ClipboardService(
        clipboard, sequence_reader=lambda: next(sequence_values)
    )

    restored = guarded_service.restore_snapshot(
        snapshot, expected_sequence_number=200
    )

    assert restored is False
    assert clipboard.mimeData().text() == "new user content"


def test_snapshot_retries_after_one_sequence_change(application):
    sequence_values = iter([1, 2, 2, 2])
    clipboard = FakeClipboard(SequenceCounter(), text_mime("stable"))
    service = ClipboardService(
        clipboard, sequence_reader=lambda: next(sequence_values)
    )

    snapshot = service.capture_snapshot(max_attempts=2)

    assert snapshot.text == "stable"
    assert snapshot.sequence_number == 2


def test_snapshot_fails_when_clipboard_never_stabilizes(application):
    value = 0

    def changing_sequence():
        nonlocal value
        value += 1
        return value

    clipboard = FakeClipboard(SequenceCounter(), text_mime("unstable"))
    service = ClipboardService(clipboard, sequence_reader=changing_sequence)

    with pytest.raises(ClipboardChangedDuringSnapshotError):
        service.capture_snapshot(max_attempts=2)


def test_read_text_returns_none_for_non_text_clipboard(application):
    sequence = SequenceCounter()
    mime_data = QMimeData()
    mime_data.setData("application/octet-stream", QByteArray(b"binary"))
    service = ClipboardService(
        FakeClipboard(sequence, mime_data), sequence_reader=sequence.read
    )

    assert service.read_text() is None
