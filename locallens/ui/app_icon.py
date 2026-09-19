"""Application icon loading for source and PyInstaller builds."""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtGui import QIcon


def application_icon_path() -> Path:
    """Return the packaged or development path for the LocalLens icon."""

    bundled_root = getattr(sys, "_MEIPASS", None)
    root = Path(bundled_root) if bundled_root else Path(__file__).resolve().parents[2]
    return root / "assets" / "locallens.ico"


def application_icon() -> QIcon:
    """Create the shared LocalLens icon without relying on the OS theme."""

    return QIcon(str(application_icon_path()))
