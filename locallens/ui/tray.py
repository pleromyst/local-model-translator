"""System tray controls for the background LocalLens process."""

from __future__ import annotations

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

from locallens.ui.app_icon import application_icon


class SystemTrayController(QObject):
    """Expose Settings and Exit while tray activation opens the window."""

    open_requested = Signal()
    settings_requested = Signal()
    exit_requested = Signal()

    def __init__(
        self,
        *,
        icon: QIcon | None = None,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        if icon is None:
            application = QApplication.instance()
            if application is None:
                raise RuntimeError("A QApplication is required for the system tray.")
            icon = application.windowIcon()
            if icon.isNull():
                icon = application_icon()

        self.tray_icon = QSystemTrayIcon(icon, self)
        self.tray_icon.setToolTip("LocalLens")

        self.menu = QMenu()
        self.settings_action = QAction("Settings", self)
        self.exit_action = QAction("Exit", self)
        self.menu.addAction(self.settings_action)
        self.menu.addSeparator()
        self.menu.addAction(self.exit_action)
        self.tray_icon.setContextMenu(self.menu)

        self.settings_action.triggered.connect(self.settings_requested)
        self.exit_action.triggered.connect(self.exit_requested)
        self.tray_icon.activated.connect(self._on_activated)

    @property
    def is_available(self) -> bool:
        return QSystemTrayIcon.isSystemTrayAvailable()

    def show(self) -> bool:
        if not self.is_available:
            return False
        self.tray_icon.show()
        return True

    def hide(self) -> None:
        self.tray_icon.hide()

    @Slot(QSystemTrayIcon.ActivationReason)
    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in {
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        }:
            self.open_requested.emit()
