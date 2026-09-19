"""Lightweight, reusable floating translation popup."""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, Qt, QTimer, Signal, Slot
from PySide6.QtGui import (
    QCloseEvent,
    QCursor,
    QGuiApplication,
    QKeyEvent,
    QKeySequence,
    QPainterPath,
    QRegion,
    QResizeEvent,
    QShowEvent,
    QShortcut,
    QTextCursor,
    QTextOption,
)
from PySide6.QtWidgets import (
    QApplication,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from locallens.ui.windows_effects import (
    apply_rounded_window_region,
)
from locallens.ui.app_icon import application_icon


class TranslationPopup(QWidget):
    """Frameless popup that keeps one window alive across translation states."""

    dismissed = Signal()

    _PREFERRED_WIDTH = 420
    _MINIMUM_HEIGHT = 150
    _CURSOR_OFFSET = 16
    _CORNER_RADIUS = 22

    def __init__(
        self,
        *,
        copy_text: Callable[[str], None] | None = None,
        acrylic_enabler: Callable[[int], bool] | None = None,
    ) -> None:
        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint
            | Qt.WindowType.WindowStaysOnTopHint
            | Qt.WindowType.Tool,
        )
        self.setObjectName("translationPopup")
        self.setWindowIcon(application_icon())
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setMinimumWidth(280)
        self.setMaximumWidth(520)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

        self._result_text = ""
        self._copy_text = copy_text or self._copy_to_application_clipboard
        self._acrylic_enabler = acrylic_enabler
        self._acrylic_attempted = False
        self._acrylic_enabled = False
        self._anchor: QPoint | None = None
        self._available_geometry: QRect | None = None
        self._drag_offset: QPoint | None = None
        self._manually_positioned = False

        self.title_label = QLabel("LocalLens")
        self.title_label.setObjectName("popupTitle")

        self.close_button = QPushButton("×")
        self.close_button.setObjectName("popupCloseButton")
        self.close_button.setAccessibleName("Close")
        self.close_button.setFixedSize(28, 28)
        self.close_button.clicked.connect(self.close)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.addWidget(self.title_label)
        header.addStretch(1)
        header.addWidget(self.close_button)

        self.header_widget = QWidget()
        self.header_widget.setObjectName("popupHeader")
        self.header_widget.setLayout(header)
        self.header_widget.setCursor(Qt.CursorShape.OpenHandCursor)
        self.header_widget.installEventFilter(self)
        self.title_label.setCursor(Qt.CursorShape.OpenHandCursor)
        self.title_label.installEventFilter(self)

        self.body_edit = QTextEdit()
        self.body_edit.setObjectName("popupBody")
        self.body_edit.setReadOnly(True)
        self.body_edit.setAcceptRichText(False)
        self.body_edit.setFrameShape(QFrame.Shape.NoFrame)
        self.body_edit.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        self.body_edit.setWordWrapMode(
            QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere
        )
        self.body_edit.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )
        self.body_edit.setVerticalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAsNeeded
        )
        self.body_edit.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
            | Qt.TextInteractionFlag.TextSelectableByKeyboard
        )
        self.body_edit.setMinimumHeight(54)

        self.copy_button = QPushButton("Copy")
        self.copy_button.setObjectName("popupCopyButton")
        self.copy_button.setEnabled(False)
        self.copy_button.clicked.connect(self._copy_result)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addWidget(self.copy_button)
        footer.addStretch(1)

        content_layout = QVBoxLayout()
        content_layout.setContentsMargins(16, 12, 16, 14)
        content_layout.setSpacing(9)
        content_layout.addWidget(self.header_widget)
        content_layout.addWidget(self.body_edit, 1)
        content_layout.addLayout(footer)

        self.content_frame = QFrame()
        self.content_frame.setObjectName("popupContent")
        self.content_frame.setLayout(content_layout)

        outer_layout = QVBoxLayout()
        # Native Acrylic is applied to the complete top-level HWND.  Keeping a
        # transparent shadow margin here would therefore produce a rectangular
        # tinted halo around the rounded card.
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self.content_frame)
        self.setLayout(outer_layout)

        self._apply_visual_style(acrylic=False)

        self._copy_feedback_timer = QTimer(self)
        self._copy_feedback_timer.setSingleShot(True)
        self._copy_feedback_timer.setInterval(1000)
        self._copy_feedback_timer.timeout.connect(self._restore_copy_label)

        self._escape_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self)
        self._escape_shortcut.setContext(Qt.ShortcutContext.WindowShortcut)
        self._escape_shortcut.activated.connect(self.close)
        self.resize(self._PREFERRED_WIDTH, self._MINIMUM_HEIGHT)

    @property
    def acrylic_enabled(self) -> bool:
        return self._acrylic_enabled

    def _apply_visual_style(self, *, acrylic: bool) -> None:
        frame_alpha = 82 if acrylic else 218
        style = """
            QFrame#popupContent {
                background-color: rgba(24, 20, 34, FRAME_ALPHA);
                border: 1px solid rgba(214, 196, 255, 46);
                border-radius: CORNER_RADIUSpx;
            }
            QLabel#popupTitle {
                color: rgba(255, 255, 255, 235);
                font-size: 13px;
                font-weight: 600;
            }
            QTextEdit#popupBody {
                background: transparent;
                color: rgba(255, 255, 255, 232);
                selection-background-color: rgba(93, 139, 255, 180);
                font-size: 14px;
                padding: 0;
            }
            QPushButton {
                color: rgba(255, 255, 255, 205);
                background-color: rgba(255, 255, 255, 20);
                border: 1px solid rgba(232, 222, 255, 34);
                border-radius: 8px;
                padding: 5px 10px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 32);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 44);
            }
            QPushButton:disabled {
                color: rgba(255, 255, 255, 90);
                background-color: rgba(255, 255, 255, 8);
            }
            QPushButton#popupCloseButton {
                color: rgba(255, 255, 255, 190);
                background: transparent;
                border: none;
                font-size: 20px;
                padding: 0;
            }
            QPushButton#popupCloseButton:hover {
                background-color: rgba(255, 255, 255, 22);
            }
            """
        self.setStyleSheet(
            style.replace("FRAME_ALPHA", str(frame_alpha)).replace(
                "CORNER_RADIUS", str(self._CORNER_RADIUS)
            )
        )

    @property
    def result_text(self) -> str:
        return self._result_text

    def show_translating(
        self,
        *,
        anchor: QPoint | None = None,
        available_geometry: QRect | None = None,
    ) -> None:
        """Show or update this popup in its in-progress state."""

        self._show_progress(
            "Translating...",
            anchor=anchor,
            available_geometry=available_geometry,
        )

    def show_reading_selection(
        self,
        *,
        anchor: QPoint | None = None,
        available_geometry: QRect | None = None,
    ) -> None:
        """Show immediately while selected text is being acquired."""

        self._show_progress(
            "Reading selected text...",
            anchor=anchor,
            available_geometry=available_geometry,
        )

    def show_recognizing(
        self,
        *,
        anchor: QPoint | None = None,
        available_geometry: QRect | None = None,
    ) -> None:
        """Show the first state of the OCR-to-translation workflow."""

        self._show_progress(
            "Recognizing text...",
            anchor=anchor,
            available_geometry=available_geometry,
        )

    def _show_progress(
        self,
        message: str,
        *,
        anchor: QPoint | None,
        available_geometry: QRect | None,
    ) -> None:
        """Update the reusable popup without creating another native window."""

        self._result_text = ""
        self.body_edit.setPlainText(message)
        self.copy_button.setEnabled(False)
        self._restore_copy_label()
        if anchor is not None or self._anchor is None:
            self._anchor = QPoint(anchor or QCursor.pos())
            self._available_geometry = (
                QRect(available_geometry) if available_geometry is not None else None
            )
            self._manually_positioned = False
        self._refresh_geometry()
        if not self.isVisible():
            self.show()
        self.raise_()

    def show_translation(self, translation: str) -> None:
        self._result_text = translation
        self.body_edit.setPlainText(translation)
        self.body_edit.moveCursor(QTextCursor.MoveOperation.Start)
        self.body_edit.horizontalScrollBar().setValue(0)
        self.copy_button.setEnabled(bool(translation))
        self._refresh_geometry()

    def show_error(
        self,
        message: str,
        *,
        anchor: QPoint | None = None,
        available_geometry: QRect | None = None,
    ) -> None:
        self._result_text = ""
        self.body_edit.setPlainText(message)
        self.copy_button.setEnabled(False)
        if anchor is not None or self._anchor is None:
            self._anchor = QPoint(anchor or QCursor.pos())
            self._available_geometry = (
                QRect(available_geometry) if available_geometry is not None else None
            )
            self._manually_positioned = False
        self._refresh_geometry()
        if not self.isVisible():
            self.show()
        self.raise_()

    @staticmethod
    def calculate_position(
        anchor: QPoint,
        popup_size: QSize,
        available_geometry: QRect,
        *,
        offset: int = _CURSOR_OFFSET,
    ) -> QPoint:
        """Place beside the cursor, flipping and clamping within one screen."""

        left = available_geometry.left()
        top = available_geometry.top()
        right = available_geometry.right() + 1
        bottom = available_geometry.bottom() + 1

        x = anchor.x() + offset
        if x + popup_size.width() > right:
            x = anchor.x() - popup_size.width() - offset
        y = anchor.y() + offset
        if y + popup_size.height() > bottom:
            y = anchor.y() - popup_size.height() - offset

        max_x = max(left, right - popup_size.width())
        max_y = max(top, bottom - popup_size.height())
        return QPoint(min(max(x, left), max_x), min(max(y, top), max_y))

    def _refresh_geometry(self) -> None:
        anchor = self._anchor or QCursor.pos()
        geometry = self._available_geometry
        if geometry is None:
            screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
            if screen is None:
                return
            geometry = screen.availableGeometry()

        maximum_height = max(
            self._MINIMUM_HEIGHT, int(round(geometry.height() * 0.45))
        )
        self.setMaximumHeight(maximum_height)
        body_maximum = max(54, maximum_height - 112)
        self.body_edit.setMaximumHeight(body_maximum)

        # Size the shell first, then wrap against the real text viewport.  A
        # small permanent reserve keeps wrapped text clear of the vertical
        # scrollbar when long results make it appear.
        self.resize(self._PREFERRED_WIDTH, self.height())
        self.layout().activate()
        document = self.body_edit.document()
        scrollbar_reserve = self.body_edit.verticalScrollBar().sizeHint().width()
        text_width = max(
            1,
            self.body_edit.viewport().width() - scrollbar_reserve - 4,
        )
        document.setTextWidth(text_width)
        body_height = max(54, min(body_maximum, int(document.size().height()) + 12))
        self.body_edit.setFixedHeight(body_height)
        desired_height = min(maximum_height, body_height + 112)
        self.resize(self._PREFERRED_WIDTH, desired_height)
        if self._manually_positioned:
            self.move(self.clamp_position(self.pos(), self.size(), geometry))
        else:
            self.move(self.calculate_position(anchor, self.size(), geometry))

    @staticmethod
    def clamp_position(
        position: QPoint,
        popup_size: QSize,
        available_geometry: QRect,
    ) -> QPoint:
        """Keep a manually positioned popup fully inside one work area."""

        max_x = max(
            available_geometry.left(),
            available_geometry.right() + 1 - popup_size.width(),
        )
        max_y = max(
            available_geometry.top(),
            available_geometry.bottom() + 1 - popup_size.height(),
        )
        return QPoint(
            min(max(position.x(), available_geometry.left()), max_x),
            min(max(position.y(), available_geometry.top()), max_y),
        )

    def eventFilter(self, watched: object, event: QEvent) -> bool:  # noqa: N802
        """Use the title area as a drag handle without blocking its buttons."""

        if watched in {self.header_widget, self.title_label}:
            if (
                event.type() == QEvent.Type.MouseButtonPress
                and event.button() == Qt.MouseButton.LeftButton
            ):
                self._drag_offset = (
                    event.globalPosition().toPoint() - self.frameGeometry().topLeft()
                )
                self._manually_positioned = True
                self.header_widget.setCursor(Qt.CursorShape.ClosedHandCursor)
                self.title_label.setCursor(Qt.CursorShape.ClosedHandCursor)
                event.accept()
                return True
            if (
                event.type() == QEvent.Type.MouseMove
                and self._drag_offset is not None
                and event.buttons() & Qt.MouseButton.LeftButton
            ):
                global_position = event.globalPosition().toPoint()
                screen = (
                    QGuiApplication.screenAt(global_position)
                    or QGuiApplication.primaryScreen()
                )
                if screen is not None:
                    geometry = screen.availableGeometry()
                    self._available_geometry = QRect(geometry)
                    position = global_position - self._drag_offset
                    self.move(self.clamp_position(position, self.size(), geometry))
                event.accept()
                return True
            if (
                event.type() == QEvent.Type.MouseButtonRelease
                and event.button() == Qt.MouseButton.LeftButton
                and self._drag_offset is not None
            ):
                self._drag_offset = None
                self.header_widget.setCursor(Qt.CursorShape.OpenHandCursor)
                self.title_label.setCursor(Qt.CursorShape.OpenHandCursor)
                event.accept()
                return True
        return super().eventFilter(watched, event)

    @Slot()
    def _copy_result(self) -> None:
        if not self._result_text:
            return
        self._copy_text(self._result_text)
        self.copy_button.setText("Copied")
        self._copy_feedback_timer.start()

    @staticmethod
    def _copy_to_application_clipboard(text: str) -> None:
        application = QApplication.instance()
        if application is None:
            return
        application.clipboard().setText(text)

    @Slot()
    def _restore_copy_label(self) -> None:
        self.copy_button.setText("Copy")

    def keyPressEvent(self, event: QKeyEvent) -> None:  # noqa: N802
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            event.accept()
            return
        super().keyPressEvent(event)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        if not self._acrylic_attempted:
            self._acrylic_attempted = True
            try:
                self._acrylic_enabled = bool(
                    self._acrylic_enabler is not None
                    and self._acrylic_enabler(int(self.winId()))
                )
            except Exception:
                self._acrylic_enabled = False
            self._apply_visual_style(acrylic=self._acrylic_enabled)

        # Keep the shape synchronized when the reusable window is shown.
        self._apply_rounded_shape()
        QTimer.singleShot(0, self._apply_rounded_shape)

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        """Keep the native Acrylic surface clipped to the rounded card."""

        super().resizeEvent(event)
        self._apply_rounded_shape()

    def _apply_rounded_shape(self) -> None:
        path = QPainterPath()
        path.addRoundedRect(
            QRectF(self.rect()), self._CORNER_RADIUS, self._CORNER_RADIUS
        )
        self.setMask(QRegion(path.toFillPolygon().toPolygon()))
        scale = self.devicePixelRatioF()
        apply_rounded_window_region(
            int(self.winId()),
            round(self.width() * scale),
            round(self.height() * scale),
            round(self._CORNER_RADIUS * scale),
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self._copy_feedback_timer.stop()
        self._restore_copy_label()
        self._result_text = ""
        self.body_edit.clear()
        self._anchor = None
        self._available_geometry = None
        self._drag_offset = None
        self._manually_positioned = False
        self.dismissed.emit()
        super().closeEvent(event)
