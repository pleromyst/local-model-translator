"""LocalLens application entry point."""

from __future__ import annotations

import os
import sys
from collections.abc import Sequence

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

from locallens import __version__
from locallens.autostart import AutoStartError, sync_autostart
from locallens.config import load_settings
from locallens.logging_config import (
    configure_logging,
    install_exception_hooks,
    set_logging_level,
    shutdown_logging,
)
from locallens.single_instance import DEFAULT_SERVER_NAME, SingleInstance
from locallens.start_menu import StartMenuShortcutError, ensure_start_menu_shortcut
from locallens.ui.app_icon import application_icon
from locallens.ui.main_window import MainWindow
from locallens.ui.tray import SystemTrayController


def _run_package_check() -> int:
    """Exercise dependencies that can otherwise fail only after packaging."""

    logger = configure_logging()
    try:
        from locallens.hotkeys import parse_hotkey
        from locallens.services.ocr_service import OCRService

        settings = load_settings()
        parse_hotkey(settings.translation_hotkey)
        parse_hotkey(settings.ocr_hotkey)

        tray = SystemTrayController()
        menu_actions = [
            action.text()
            for action in tray.menu.actions()
            if not action.isSeparator()
        ]
        if menu_actions != ["Settings", "Exit"] or tray.tray_icon.icon().isNull():
            raise RuntimeError("The packaged tray resources are unavailable.")

        OCRService()
    except Exception as exc:
        logger.error(
            "package_check status=failed exception_type=%s",
            type(exc).__name__,
        )
        return 1
    finally:
        shutdown_logging()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv if argv is None else argv)
    smoke_test = "--smoke-test" in arguments
    package_check = "--package-check" in arguments
    background_start = "--background" in arguments
    internal_arguments = {"--smoke-test", "--package-check", "--background"}
    qt_arguments = [
        argument for argument in arguments if argument not in internal_arguments
    ]

    application = QApplication(qt_arguments)
    application.setApplicationName("LocalLens")
    application.setOrganizationName("LocalLens")
    application.setWindowIcon(application_icon())
    application.setQuitOnLastWindowClosed(False)

    if package_check:
        return _run_package_check()

    server_name = (
        f"{DEFAULT_SERVER_NAME}.Smoke.{os.getpid()}"
        if smoke_test
        else DEFAULT_SERVER_NAME
    )
    single_instance = SingleInstance(server_name)
    if not single_instance.acquire():
        return 0

    logger = configure_logging()
    install_exception_hooks()
    logger.info("application_start version=%s", __version__)
    settings = load_settings()
    set_logging_level(settings.debug)
    logger.info("application_configuration model=%s", settings.model)
    if not smoke_test:
        try:
            sync_autostart(settings.start_with_windows)
        except AutoStartError:
            logger.warning("autostart_sync_failed")
        try:
            ensure_start_menu_shortcut()
        except StartMenuShortcutError:
            logger.warning("start_menu_shortcut_sync_failed")

    window = MainWindow(settings)
    tray = SystemTrayController()
    tray_available = tray.show()
    if not tray_available:
        logger.warning("system_tray_unavailable")
    window.set_close_to_tray(tray_available)

    single_instance.activation_requested.connect(window.show_from_background)
    tray.open_requested.connect(window.show_from_background)
    tray.settings_requested.connect(window.open_settings)
    tray.exit_requested.connect(window.request_exit)

    cleanup_done = False

    def cleanup() -> None:
        nonlocal cleanup_done
        if cleanup_done:
            return
        cleanup_done = True
        logger.info("application_stop")
        tray.hide()
        single_instance.close()
        shutdown_logging()

    def finish_exit() -> None:
        cleanup()
        application.quit()

    window.exit_ready.connect(finish_exit)
    application.aboutToQuit.connect(cleanup)
    if not background_start:
        window.show()
    window.start_hotkeys()

    if smoke_test:
        QTimer.singleShot(750, window.request_exit)

    return application.exec()


if __name__ == "__main__":
    raise SystemExit(main())
