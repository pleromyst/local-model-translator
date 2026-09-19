"""Current-user Start Menu shortcut for packaged LocalLens builds."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from pathlib import Path


logger = logging.getLogger("locallens.start_menu")


class StartMenuShortcutError(RuntimeError):
    """Windows could not create the LocalLens Start Menu shortcut."""


def _shortcut_path() -> Path:
    return (
        Path(os.environ["APPDATA"])
        / "Microsoft"
        / "Windows"
        / "Start Menu"
        / "Programs"
        / "LocalLens.lnk"
    )


def _powershell_literal(value: str) -> str:
    return value.replace("'", "''")


def ensure_start_menu_shortcut() -> bool:
    """Create or update the packaged app's current-user Start Menu shortcut."""

    if not getattr(sys, "frozen", False):
        return False

    executable = Path(sys.executable).resolve()
    shortcut = _shortcut_path()
    command = f"""
$shortcutPath = '{_powershell_literal(str(shortcut))}'
$shortcut = (New-Object -ComObject WScript.Shell).CreateShortcut($shortcutPath)
$shortcut.TargetPath = '{_powershell_literal(str(executable))}'
$shortcut.WorkingDirectory = '{_powershell_literal(str(executable.parent))}'
$shortcut.IconLocation = '{_powershell_literal(str(executable))},0'
$shortcut.Save()
"""
    try:
        subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise StartMenuShortcutError(
            "Could not create the LocalLens Start Menu shortcut."
        ) from exc

    logger.info("start_menu_shortcut_synced")
    return True
