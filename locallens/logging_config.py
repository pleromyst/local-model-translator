"""Privacy-preserving, size-limited LocalLens logging."""

from __future__ import annotations

import logging
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path

from locallens.config import PROJECT_ROOT


LOGGER_NAME = "locallens"
DEFAULT_LOG_PATH = PROJECT_ROOT / "logs" / "locallens.log"
MAX_LOG_BYTES = 2 * 1024 * 1024
BACKUP_COUNT = 3
_HANDLER_MARKER = "_locallens_file_handler"


def configure_logging(
    *,
    debug: bool = False,
    path: str | Path = DEFAULT_LOG_PATH,
) -> logging.Logger:
    """Configure one UTF-8 rotating handler without ever formatting user data."""

    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.DEBUG if debug else logging.INFO)
    logger.propagate = False

    for handler in tuple(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()

    try:
        log_path = Path(path)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler: logging.Handler = RotatingFileHandler(
            log_path,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
    except OSError:
        # Logging must never prevent the local translation tool from starting.
        handler = logging.NullHandler()

    setattr(handler, _HANDLER_MARKER, True)
    handler.setLevel(logging.DEBUG if debug else logging.INFO)
    handler.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
    )
    logger.addHandler(handler)
    return logger


def set_logging_level(debug: bool) -> None:
    level = logging.DEBUG if debug else logging.INFO
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(level)
    for handler in logger.handlers:
        if getattr(handler, _HANDLER_MARKER, False):
            handler.setLevel(level)


def install_exception_hooks() -> None:
    """Record exception types without leaking exception messages or user text."""

    logger = logging.getLogger(f"{LOGGER_NAME}.exceptions")
    original_sys_hook = sys.excepthook
    original_thread_hook = threading.excepthook

    def sys_hook(exception_type, exception, traceback) -> None:
        logger.critical(
            "uncaught_exception exception_type=%s",
            exception_type.__name__,
        )
        original_sys_hook(exception_type, exception, traceback)

    def thread_hook(args: threading.ExceptHookArgs) -> None:
        logger.critical(
            "uncaught_thread_exception thread=%s exception_type=%s",
            args.thread.name if args.thread is not None else "unknown",
            args.exc_type.__name__,
        )
        original_thread_hook(args)

    sys.excepthook = sys_hook
    threading.excepthook = thread_hook


def shutdown_logging() -> None:
    logger = logging.getLogger(LOGGER_NAME)
    for handler in tuple(logger.handlers):
        if getattr(handler, _HANDLER_MARKER, False):
            logger.removeHandler(handler)
            handler.close()
