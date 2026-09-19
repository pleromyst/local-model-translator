"""Minimal full-screen overlay for selecting one screenshot region."""

from __future__ import annotations

from PySide6.QtCore import QPoint, QRect, Qt, Signal
from PySide6.QtGui import (
    QColor,
    QCloseEvent,
    QKeyEvent,
    QMouseEvent,
    QPaintEvent,
    QPainter,
    QPen,
)
from PySide6.QtWidgets import QWidget

from locallens.services.screenshot_service import ScreenCapture


class CaptureOverlay(QWidget):
    """Display one captured screen and return a DPI-correct selected image."""

    captured = Signal(object)
    cancelled = Signal()
    empty_selection = Signal()

    def __init__(self, capture: ScreenCapture) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setObjectName("captureOverlay")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setCursor(Qt.CursorShape.CrossCursor)
        self.setMouseTracking(True)

        self._capture = capture
        self._dragging = False
        self._start = QPoint()
        self._selection = QRect()
        self._completed = False
        self._hint = "Drag to select an area  •  Esc to cancel"
        self.setGeometry(capture.screen_geometry)

    @property
    def selection(self) -> QRect:
        return QRect(self._selection)

    def start(self) -> None:
        self.setGeometry(self._capture.screen_geometry)
        self.show()
        self.raise_()
        self.activateWindow()
        self.setFocus(Qt.FocusReason.ActiveWindowFocusReason)

    def _bounded_point(self, point: QPoint) -> QPoint:
        bounds = self.rect()
        return QPoint(
            min(max(point.x(), bounds.left()), bounds.right()),
            min(max(point.y(), bounds.top()), bounds.bottom()),
        )

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._dragging = True
        self._hint = "Release to capture  •  Esc to cancel"
        self._start = self._bounded_point(event.position().toPoint())
        self._selection = QRect(self._start, self._start)
        self.update()
        event.accept()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._dragging:
            event.ignore()
            return
        end = self._bounded_point(event.position().toPoint())
        self._selection = QRect(self._start, end).normalized()
        self.update()
        event.accept()

    def mouseReleaseEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if not self._dragging or event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        self._dragging = False
        end = self._bounded_point(event.position().toPoint())
        self._selection = QRect(self._start, end).normalized()
        if self._selection.width() < 2 or self._selection.height() < 2:
            self._selection = QRect()
            self._hint = "Select a non-empty area  •  Esc to cancel"
            self.empty_selection.emit()
            self.update()
            event.accept()
            return

        image = self._capture.crop(self._selection)
        if image.isNull():
            self._selection = QRect()
            self._hint = "Select a non-empty area  •  Esc to cancel"
            self.empty_selection.emit()
            self.update()
            event.accept()
            return

        self._completed = True
        self.close()
        self.captured.emit(image)
        event.accept()

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.drawPixmap(self.rect(), self._capture.pixmap)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 112))

        if not self._selection.isEmpty():
            painter.save()
            painter.setClipRect(self._selection)
            painter.drawPixmap(self.rect(), self._capture.pixmap)
            painter.fillRect(self._selection, QColor(255, 255, 255, 18))
            painter.restore()
            painter.setPen(QPen(QColor(105, 165, 255), 2))
            painter.drawRect(self._selection.adjusted(1, 1, -1, -1))

        metrics = painter.fontMetrics()
        hint_rect = metrics.boundingRect(self._hint).adjusted(-14, -8, 14, 8)
        hint_rect.moveCenter(QPoint(self.rect().center().x(), self.rect().top() + 34))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(20, 20, 24, 210))
        painter.drawRoundedRect(hint_rect, 8, 8)
        painter.setPen(QColor(255, 255, 255, 230))
        painter.drawText(hint_rect, Qt.AlignmentFlag.AlignCenter, self._hint)

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if not self._completed:
            self.cancelled.emit()
        super().closeEvent(event)
