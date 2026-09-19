"""Best-effort native Windows visual effects with safe fallbacks."""

from __future__ import annotations

import ctypes
import logging
import sys
from ctypes import wintypes


logger = logging.getLogger("locallens.ui.effects")

_WCA_ACCENT_POLICY = 19
_ACCENT_ENABLE_ACRYLICBLURBEHIND = 4
_ACCENT_DRAW_ALL_BORDERS = 2


class _AccentPolicy(ctypes.Structure):
    _fields_ = [
        ("accent_state", ctypes.c_int),
        ("accent_flags", ctypes.c_int),
        ("gradient_color", ctypes.c_uint32),
        ("animation_id", ctypes.c_int),
    ]


class _WindowCompositionAttributeData(ctypes.Structure):
    _fields_ = [
        ("attribute", ctypes.c_int),
        ("data", ctypes.c_void_p),
        ("data_size", ctypes.c_size_t),
    ]


def _rgba_to_abgr(red: int, green: int, blue: int, alpha: int) -> int:
    channels = (red, green, blue, alpha)
    if any(isinstance(channel, bool) or not 0 <= channel <= 255 for channel in channels):
        raise ValueError("acrylic tint channels must be integers from 0 to 255")
    return (alpha << 24) | (blue << 16) | (green << 8) | red


def enable_acrylic_blur(
    window_id: int,
    *,
    tint: tuple[int, int, int, int] = (31, 24, 48, 138),
) -> bool:
    """Enable Windows Acrylic blur, returning False when it is unavailable."""

    if sys.platform != "win32" or not window_id:
        return False
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        return False

    try:
        user32 = win_dll("user32", use_last_error=True)
        setter = user32.SetWindowCompositionAttribute
        setter.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(_WindowCompositionAttributeData),
        ]
        setter.restype = wintypes.BOOL

        policy = _AccentPolicy(
            accent_state=_ACCENT_ENABLE_ACRYLICBLURBEHIND,
            accent_flags=_ACCENT_DRAW_ALL_BORDERS,
            gradient_color=_rgba_to_abgr(*tint),
            animation_id=0,
        )
        data = _WindowCompositionAttributeData(
            attribute=_WCA_ACCENT_POLICY,
            data=ctypes.cast(ctypes.pointer(policy), ctypes.c_void_p),
            data_size=ctypes.sizeof(policy),
        )
        enabled = bool(setter(wintypes.HWND(window_id), ctypes.byref(data)))
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        logger.info(
            "acrylic_effect enabled=false exception_type=%s",
            type(exc).__name__,
        )
        return False

    logger.info("acrylic_effect enabled=%s", str(enabled).lower())
    return enabled


def apply_rounded_window_region(
    window_id: int,
    width: int,
    height: int,
    radius: int,
) -> bool:
    """Clip the native HWND, including its Acrylic surface, to rounded corners."""

    if (
        sys.platform != "win32"
        or not window_id
        or width <= 0
        or height <= 0
        or radius <= 0
    ):
        return False
    win_dll = getattr(ctypes, "WinDLL", None)
    if win_dll is None:
        return False

    try:
        gdi32 = win_dll("gdi32", use_last_error=True)
        user32 = win_dll("user32", use_last_error=True)
        creator = gdi32.CreateRoundRectRgn
        creator.argtypes = [
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
        ]
        creator.restype = wintypes.HRGN
        setter = user32.SetWindowRgn
        setter.argtypes = [wintypes.HWND, wintypes.HRGN, wintypes.BOOL]
        setter.restype = ctypes.c_int
        deleter = gdi32.DeleteObject
        deleter.argtypes = [wintypes.HGDIOBJ]
        deleter.restype = wintypes.BOOL

        region = creator(0, 0, width + 1, height + 1, radius * 2, radius * 2)
        if not region:
            return False
        applied = bool(setter(wintypes.HWND(window_id), region, True))
        if not applied:
            deleter(region)
    except (AttributeError, OSError, TypeError, ValueError) as exc:
        logger.info(
            "rounded_window_region enabled=false exception_type=%s",
            type(exc).__name__,
        )
        return False

    logger.info("rounded_window_region enabled=%s", str(applied).lower())
    return applied
