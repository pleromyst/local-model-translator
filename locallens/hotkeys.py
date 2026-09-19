"""Native Win32 global hotkey registration for the Qt event loop."""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QAbstractNativeEventFilter, QCoreApplication, QObject, Signal


WM_HOTKEY = 0x0312
MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

VK_SHIFT = 0x10
VK_CONTROL = 0x11
VK_MENU = 0x12
VK_LWIN = 0x5B
VK_RWIN = 0x5C

_MODIFIER_ALIASES = {
    "alt": (MOD_ALT, VK_MENU, "Alt"),
    "ctrl": (MOD_CONTROL, VK_CONTROL, "Ctrl"),
    "control": (MOD_CONTROL, VK_CONTROL, "Ctrl"),
    "shift": (MOD_SHIFT, VK_SHIFT, "Shift"),
    "win": (MOD_WIN, VK_LWIN, "Win"),
    "windows": (MOD_WIN, VK_LWIN, "Win"),
}
logger = logging.getLogger("locallens.hotkeys")


class HotkeyBackend(Protocol):
    def register_hotkey(
        self, window_id: int, hotkey_id: int, modifiers: int, virtual_key: int
    ) -> bool: ...

    def unregister_hotkey(self, window_id: int, hotkey_id: int) -> bool: ...

    def last_error(self) -> int: ...


class HotkeyError(RuntimeError):
    """Base class for safe, user-facing hotkey errors."""


class HotkeyRegistrationError(HotkeyError):
    """The requested system-wide hotkey could not be registered."""


@dataclass(frozen=True, slots=True)
class HotkeySpec:
    display_name: str
    modifiers: int
    virtual_key: int
    modifier_virtual_keys: tuple[int, ...]


def parse_hotkey(value: str) -> HotkeySpec:
    """Parse configurable combinations such as ``Alt+T``."""

    if not isinstance(value, str):
        raise TypeError("hotkey must be a string")
    parts = [part.strip() for part in value.split("+") if part.strip()]
    if len(parts) < 2:
        raise ValueError("hotkey must contain a modifier and one key")

    modifiers = MOD_NOREPEAT
    modifier_virtual_keys: list[int] = []
    display_modifiers: list[str] = []
    key_part: str | None = None

    for part in parts:
        modifier = _MODIFIER_ALIASES.get(part.casefold())
        if modifier is not None:
            modifier_flag, modifier_key, display_name = modifier
            if modifier_flag & modifiers:
                raise ValueError(f"duplicate hotkey modifier: {display_name}")
            modifiers |= modifier_flag
            modifier_virtual_keys.append(modifier_key)
            display_modifiers.append(display_name)
            continue
        if key_part is not None:
            raise ValueError("hotkey must contain exactly one non-modifier key")
        key_part = part.upper()

    if not display_modifiers or key_part is None:
        raise ValueError("hotkey must contain a modifier and one key")

    if len(key_part) == 1 and (key_part.isascii() and key_part.isalnum()):
        virtual_key = ord(key_part)
    elif key_part.startswith("F") and key_part[1:].isdigit():
        function_number = int(key_part[1:])
        if not 1 <= function_number <= 24:
            raise ValueError("function-key hotkeys must be between F1 and F24")
        virtual_key = 0x70 + function_number - 1
    else:
        raise ValueError(f"unsupported hotkey key: {key_part}")

    display_name = "+".join([*display_modifiers, key_part])
    return HotkeySpec(
        display_name=display_name,
        modifiers=modifiers,
        virtual_key=virtual_key,
        modifier_virtual_keys=tuple(modifier_virtual_keys),
    )


class Win32HotkeyBackend:
    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Global hotkeys are only available on Windows.")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.RegisterHotKey.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_uint,
            ctypes.c_uint,
        ]
        self._user32.RegisterHotKey.restype = ctypes.c_bool
        self._user32.UnregisterHotKey.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self._user32.UnregisterHotKey.restype = ctypes.c_bool

    def register_hotkey(
        self, window_id: int, hotkey_id: int, modifiers: int, virtual_key: int
    ) -> bool:
        ctypes.set_last_error(0)
        return bool(
            self._user32.RegisterHotKey(
                ctypes.c_void_p(window_id), hotkey_id, modifiers, virtual_key
            )
        )

    def unregister_hotkey(self, window_id: int, hotkey_id: int) -> bool:
        ctypes.set_last_error(0)
        return bool(
            self._user32.UnregisterHotKey(ctypes.c_void_p(window_id), hotkey_id)
        )

    @staticmethod
    def last_error() -> int:
        return ctypes.get_last_error()


class _HotkeySignals(QObject):
    activated = Signal(object)


class HotkeyManager(QAbstractNativeEventFilter):
    """Bridge WM_HOTKEY messages into Qt signals."""

    def __init__(
        self,
        window_id: int,
        *,
        backend: HotkeyBackend | None = None,
        application: QCoreApplication | None = None,
    ) -> None:
        super().__init__()
        self._window_id = window_id
        self._signals = _HotkeySignals()
        self._backend = backend or Win32HotkeyBackend()
        self._application = application or QCoreApplication.instance()
        if self._application is None:
            raise RuntimeError("A Qt application is required for global hotkeys.")
        self._registrations: dict[int, HotkeySpec] = {}
        self._next_hotkey_id = 0x4C01
        self._filter_installed = False
        self._last_native_hotkey_message: tuple[int, int] | None = None

    @property
    def activated(self):
        return self._signals.activated

    def register(self, value: str) -> HotkeySpec:
        try:
            spec = parse_hotkey(value)
        except (TypeError, ValueError) as exc:
            logger.warning(
                "hotkey_registration_failed reason=invalid exception_type=%s",
                type(exc).__name__,
            )
            raise HotkeyRegistrationError(
                f"The hotkey '{value}' is invalid: {exc}"
            ) from exc
        if any(existing == spec for existing in self._registrations.values()):
            logger.warning(
                "hotkey_registration_failed reason=duplicate shortcut=%s",
                spec.display_name,
            )
            raise HotkeyRegistrationError(
                f"The hotkey {spec.display_name} is already registered by LocalLens."
            )

        hotkey_id = self._next_hotkey_id
        self._next_hotkey_id += 1
        if not self._backend.register_hotkey(
            self._window_id,
            hotkey_id,
            spec.modifiers,
            spec.virtual_key,
        ):
            error_code = self._backend.last_error()
            logger.warning(
                "hotkey_registration_failed reason=system_conflict shortcut=%s "
                "win32_error=%d",
                spec.display_name,
                error_code,
            )
            raise HotkeyRegistrationError(
                f"The hotkey {spec.display_name} is already in use. "
                f"(Win32 error {error_code})"
            )

        self._registrations[hotkey_id] = spec
        logger.info("hotkey_registered shortcut=%s", spec.display_name)
        if not self._filter_installed:
            self._application.installNativeEventFilter(self)
            self._filter_installed = True
        return spec

    def unregister_all(self) -> None:
        for hotkey_id in tuple(self._registrations):
            self._backend.unregister_hotkey(self._window_id, hotkey_id)
            self._registrations.pop(hotkey_id, None)
        if self._filter_installed:
            self._application.removeNativeEventFilter(self)
            self._filter_installed = False
        self._last_native_hotkey_message = None

    def nativeEventFilter(self, event_type, message):  # noqa: N802
        if bytes(event_type) not in {
            b"windows_generic_MSG",
            b"windows_dispatcher_MSG",
        }:
            return False, 0
        try:
            native_message = ctypes.cast(
                int(message), ctypes.POINTER(wintypes.MSG)
            ).contents
        except (TypeError, ValueError):
            return False, 0
        if native_message.message == WM_HOTKEY:
            message_token = (
                int(native_message.wParam),
                int(native_message.time),
            )
            # Qt can expose the same system-wide WM_HOTKEY through both
            # windows_dispatcher_MSG and windows_generic_MSG. The original
            # Win32 timestamp identifies that duplicate without suppressing a
            # later, intentional press of the same shortcut.
            if message_token != self._last_native_hotkey_message:
                self._last_native_hotkey_message = message_token
                self.dispatch_hotkey(int(native_message.wParam))
        return False, 0

    def dispatch_hotkey(self, hotkey_id: int) -> bool:
        """Dispatch a registered ID; separated for deterministic tests."""

        spec = self._registrations.get(hotkey_id)
        if spec is None:
            return False
        logger.info("hotkey_event shortcut=%s", spec.display_name)
        self.activated.emit(spec)
        return True
