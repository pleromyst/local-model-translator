"""Windows sign-in startup registration for packaged LocalLens builds."""

from __future__ import annotations

import logging
import sys
from pathlib import Path

try:
    import winreg
except ImportError:  # pragma: no cover - LocalLens is Windows-only.
    winreg = None  # type: ignore[assignment]


logger = logging.getLogger("locallens.autostart")
_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "LocalLens"


class AutoStartError(RuntimeError):
    """Windows could not update the current user's sign-in startup entry."""


def sync_autostart(enabled: bool) -> bool:
    """Synchronize the packaged app's current-user Run entry.

    Source runs deliberately do not create a startup entry because their Python
    executable and working directory are not a portable application install.
    """

    if not getattr(sys, "frozen", False) or winreg is None:
        return False

    executable = Path(sys.executable).resolve()
    command = f'"{executable}" --background'
    try:
        with winreg.CreateKeyEx(
            winreg.HKEY_CURRENT_USER,
            _RUN_KEY,
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            if enabled:
                winreg.SetValueEx(key, _VALUE_NAME, 0, winreg.REG_SZ, command)
            else:
                try:
                    winreg.DeleteValue(key, _VALUE_NAME)
                except FileNotFoundError:
                    pass
    except OSError as exc:
        raise AutoStartError(
            "Could not update LocalLens sign-in startup."
        ) from exc

    logger.info("autostart_synced enabled=%s", str(enabled).lower())
    return True
