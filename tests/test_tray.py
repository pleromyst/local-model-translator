import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

from locallens.ui.tray import SystemTrayController


@pytest.fixture(scope="module")
def application():
    app = QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    return app


def test_tray_menu_exposes_settings_and_exit(application):
    tray = SystemTrayController()
    events = []
    tray.settings_requested.connect(lambda: events.append("settings"))
    tray.exit_requested.connect(lambda: events.append("exit"))

    tray.settings_action.trigger()
    tray.exit_action.trigger()

    assert [action.text() for action in tray.menu.actions() if not action.isSeparator()] == [
        "Settings",
        "Exit",
    ]
    assert events == ["settings", "exit"]
    assert tray.tray_icon.toolTip() == "LocalLens"
    assert not tray.tray_icon.icon().isNull()
    tray.hide()


def test_clicking_tray_icon_requests_open(application):
    tray = SystemTrayController()
    opened = []
    tray.open_requested.connect(lambda: opened.append(True))

    tray._on_activated(QSystemTrayIcon.ActivationReason.Trigger)

    assert opened == [True]
    tray.hide()
