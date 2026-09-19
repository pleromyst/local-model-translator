"""Current-screen capture and DPI-aware logical-region cropping."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from pathlib import Path

from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QCursor, QGuiApplication, QImage, QPixmap, QScreen

from locallens.config import PROJECT_ROOT


DEFAULT_DEBUG_CAPTURE_PATH = PROJECT_ROOT / "logs" / "debug_capture.png"
logger = logging.getLogger("locallens.screenshot")


class ScreenshotError(RuntimeError):
    """A screen image could not be captured or saved."""


@dataclass(frozen=True, slots=True)
class ScreenCapture:
    """A physical-pixel screenshot paired with its logical desktop geometry."""

    screen_name: str
    screen_geometry: QRect
    pixmap: QPixmap

    @property
    def logical_bounds(self) -> QRect:
        return QRect(QPoint(0, 0), self.screen_geometry.size())

    @property
    def scale_x(self) -> float:
        width = self.screen_geometry.width()
        return self.pixmap.width() / width if width > 0 else 1.0

    @property
    def scale_y(self) -> float:
        height = self.screen_geometry.height()
        return self.pixmap.height() / height if height > 0 else 1.0

    def global_to_local(self, global_rect: QRect) -> QRect:
        return QRect(global_rect).translated(-self.screen_geometry.topLeft())

    def crop(self, logical_rect: QRect) -> QImage:
        """Crop a logical local rectangle into full-resolution image pixels."""

        selection = QRect(logical_rect).normalized().intersected(self.logical_bounds)
        if selection.width() < 2 or selection.height() < 2:
            return QImage()

        left = math.floor(selection.left() * self.scale_x)
        top = math.floor(selection.top() * self.scale_y)
        right = math.ceil((selection.right() + 1) * self.scale_x)
        bottom = math.ceil((selection.bottom() + 1) * self.scale_y)
        pixel_rect = QRect(left, top, right - left, bottom - top).intersected(
            self.pixmap.rect()
        )
        if pixel_rect.width() < 1 or pixel_rect.height() < 1:
            return QImage()

        image = self.pixmap.copy(pixel_rect).toImage()
        # OCR consumes physical pixels; do not let a retained DPR make callers
        # interpret the image as a smaller logical surface.
        image.setDevicePixelRatio(1.0)
        return image


class ScreenshotService:
    """Capture the display containing the cursor without persisting by default."""

    def capture_current_screen(self, position: QPoint | None = None) -> ScreenCapture:
        anchor = QPoint(position or QCursor.pos())
        screen = QGuiApplication.screenAt(anchor) or QGuiApplication.primaryScreen()
        if screen is None:
            logger.warning("screenshot_capture_failed reason=no_display")
            raise ScreenshotError("No display is available for screen capture.")
        return self.capture_screen(screen)

    @staticmethod
    def capture_screen(screen: QScreen) -> ScreenCapture:
        geometry = QRect(screen.geometry())
        if geometry.isEmpty():
            logger.warning("screenshot_capture_failed reason=invalid_geometry")
            raise ScreenshotError("The selected display has an invalid geometry.")
        pixmap = screen.grabWindow(0)
        if pixmap.isNull():
            logger.warning("screenshot_capture_failed reason=null_pixmap")
            raise ScreenshotError("The current display could not be captured.")
        logger.info("screenshot_captured screen=%s", screen.name())
        return ScreenCapture(screen.name(), geometry, pixmap)

    @staticmethod
    def save_debug_capture(
        image: QImage,
        path: str | Path = DEFAULT_DEBUG_CAPTURE_PATH,
    ) -> Path:
        if image.isNull():
            logger.warning("debug_capture_save_failed reason=empty_image")
            raise ScreenshotError("An empty capture cannot be saved.")
        output_path = Path(path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        if not image.save(str(output_path), "PNG"):
            logger.warning("debug_capture_save_failed reason=write_failed")
            raise ScreenshotError("The debug capture could not be saved.")
        logger.info("debug_capture_saved")
        return output_path

    @staticmethod
    def remove_debug_capture(
        path: str | Path = DEFAULT_DEBUG_CAPTURE_PATH,
    ) -> bool:
        """Remove the opt-in debug image when debug capture is disabled."""

        output_path = Path(path)
        existed = output_path.exists()
        try:
            output_path.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning(
                "debug_capture_cleanup_failed exception_type=%s",
                type(exc).__name__,
            )
            return False
        if existed:
            logger.info("debug_capture_removed")
        return True
