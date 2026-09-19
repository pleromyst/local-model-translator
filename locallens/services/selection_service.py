"""Asynchronous selected-text acquisition through safe clipboard handling."""

from __future__ import annotations

import ctypes
import logging
import sys
import time
from ctypes import wintypes
from dataclasses import dataclass
from typing import Protocol

from PySide6.QtCore import QObject, QTimer, Signal, Slot

from locallens.services.clipboard_service import (
    ClipboardError,
    ClipboardService,
    ClipboardSnapshot,
)


VK_C = 0x43
VK_CONTROL = 0x11
INPUT_KEYBOARD = 1
KEYEVENTF_KEYUP = 0x0002
WM_CANCELMODE = 0x001F
GUI_INMENUMODE = 0x0004
GUI_SYSTEMMENUMODE = 0x0008

NO_TEXT_MESSAGE = (
    "No selectable text was detected.\n\n"
    "If the target app is running as administrator or does not support text "
    "selection, try OCR mode with Alt+Q."
)
logger = logging.getLogger("locallens.selection")


class InputBackend(Protocol):
    def is_key_down(self, virtual_key: int) -> bool: ...

    def capture_target(self) -> "FocusTarget | None": ...

    def restore_target(self, target: "FocusTarget | None") -> bool: ...

    def send_ctrl_c(self) -> bool: ...


class _KeyboardInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [
        ("mi", _MouseInput),
        ("ki", _KeyboardInput),
        ("hi", _HardwareInput),
    ]


class _Input(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("type", wintypes.DWORD), ("data", _InputUnion)]


class _GuiThreadInfo(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("flags", wintypes.DWORD),
        ("hwndActive", wintypes.HWND),
        ("hwndFocus", wintypes.HWND),
        ("hwndCapture", wintypes.HWND),
        ("hwndMenuOwner", wintypes.HWND),
        ("hwndMoveSize", wintypes.HWND),
        ("hwndCaret", wintypes.HWND),
        ("rcCaret", wintypes.RECT),
    ]


@dataclass(frozen=True, slots=True)
class FocusTarget:
    """Foreground window and keyboard focus captured when the hotkey fires."""

    foreground_window: int
    focus_window: int
    thread_id: int


class Win32InputBackend:
    """Minimal SendInput adapter; UIPI failures surface as capture timeouts."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Selected-text capture is only available on Windows.")
        self._user32 = ctypes.WinDLL("user32", use_last_error=True)
        self._user32.GetAsyncKeyState.argtypes = [ctypes.c_int]
        self._user32.GetAsyncKeyState.restype = wintypes.SHORT
        self._user32.SendInput.argtypes = [
            wintypes.UINT,
            ctypes.POINTER(_Input),
            ctypes.c_int,
        ]
        self._user32.SendInput.restype = wintypes.UINT
        self._user32.GetForegroundWindow.argtypes = []
        self._user32.GetForegroundWindow.restype = wintypes.HWND
        self._user32.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND,
            ctypes.POINTER(wintypes.DWORD),
        ]
        self._user32.GetWindowThreadProcessId.restype = wintypes.DWORD
        self._user32.GetGUIThreadInfo.argtypes = [
            wintypes.DWORD,
            ctypes.POINTER(_GuiThreadInfo),
        ]
        self._user32.GetGUIThreadInfo.restype = wintypes.BOOL
        self._user32.IsWindow.argtypes = [wintypes.HWND]
        self._user32.IsWindow.restype = wintypes.BOOL
        self._user32.PostMessageW.argtypes = [
            wintypes.HWND,
            wintypes.UINT,
            wintypes.WPARAM,
            wintypes.LPARAM,
        ]
        self._user32.PostMessageW.restype = wintypes.BOOL
        self._user32.SetForegroundWindow.argtypes = [wintypes.HWND]
        self._user32.SetForegroundWindow.restype = wintypes.BOOL
        self._user32.SetFocus.argtypes = [wintypes.HWND]
        self._user32.SetFocus.restype = wintypes.HWND
        self._user32.AttachThreadInput.argtypes = [
            wintypes.DWORD,
            wintypes.DWORD,
            wintypes.BOOL,
        ]
        self._user32.AttachThreadInput.restype = wintypes.BOOL

        self._kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._kernel32.GetCurrentThreadId.argtypes = []
        self._kernel32.GetCurrentThreadId.restype = wintypes.DWORD

    def is_key_down(self, virtual_key: int) -> bool:
        return bool(self._user32.GetAsyncKeyState(virtual_key) & 0x8000)

    def capture_target(self) -> FocusTarget | None:
        foreground = int(self._user32.GetForegroundWindow() or 0)
        if not foreground:
            return None
        thread_id = int(
            self._user32.GetWindowThreadProcessId(wintypes.HWND(foreground), None)
        )
        if not thread_id:
            return None
        info = self._gui_thread_info(thread_id)
        focus = int(info.hwndFocus or 0) if info is not None else 0
        return FocusTarget(foreground, focus, thread_id)

    def restore_target(self, target: FocusTarget | None) -> bool:
        """Cancel Alt menu mode and restore the control focused at hotkey time."""

        if target is None or not self._user32.IsWindow(
            wintypes.HWND(target.foreground_window)
        ):
            return False

        current_info = self._gui_thread_info(target.thread_id)
        current_foreground = int(self._user32.GetForegroundWindow() or 0)
        current_focus = (
            int(current_info.hwndFocus or 0) if current_info is not None else 0
        )
        menu_active = bool(
            current_info is not None
            and current_info.flags & (GUI_INMENUMODE | GUI_SYSTEMMENUMODE)
        )
        if (
            current_foreground == target.foreground_window
            and (not target.focus_window or current_focus == target.focus_window)
            and not menu_active
        ):
            return True

        # Alt-based global hotkeys can leave traditional and custom title menus
        # active even though the editor selection remains visible. Cancel that
        # transient mode without injecting Escape, which can clear selections.
        self._user32.PostMessageW(
            wintypes.HWND(target.foreground_window), WM_CANCELMODE, 0, 0
        )

        current_thread = int(self._kernel32.GetCurrentThreadId())
        attached = current_thread != target.thread_id and bool(
            self._user32.AttachThreadInput(current_thread, target.thread_id, True)
        )
        if current_thread != target.thread_id and not attached:
            return False
        try:
            self._user32.SetForegroundWindow(wintypes.HWND(target.foreground_window))
            if target.focus_window and self._user32.IsWindow(
                wintypes.HWND(target.focus_window)
            ):
                ctypes.set_last_error(0)
                self._user32.SetFocus(wintypes.HWND(target.focus_window))
        finally:
            if attached:
                self._user32.AttachThreadInput(current_thread, target.thread_id, False)
        return True

    def _gui_thread_info(self, thread_id: int) -> _GuiThreadInfo | None:
        info = _GuiThreadInfo(cbSize=ctypes.sizeof(_GuiThreadInfo))
        if not self._user32.GetGUIThreadInfo(thread_id, ctypes.byref(info)):
            return None
        return info

    def send_ctrl_c(self) -> bool:
        inputs = (_Input * 4)(
            self._keyboard_event(VK_CONTROL, key_up=False),
            self._keyboard_event(VK_C, key_up=False),
            self._keyboard_event(VK_C, key_up=True),
            self._keyboard_event(VK_CONTROL, key_up=True),
        )
        sent = int(self._user32.SendInput(len(inputs), inputs, ctypes.sizeof(_Input)))
        return sent == len(inputs)

    @staticmethod
    def _keyboard_event(virtual_key: int, *, key_up: bool) -> _Input:
        return _Input(
            type=INPUT_KEYBOARD,
            data=_InputUnion(
                ki=_KeyboardInput(
                    wVk=virtual_key,
                    wScan=0,
                    dwFlags=KEYEVENTF_KEYUP if key_up else 0,
                    time=0,
                    dwExtraInfo=0,
                )
            ),
        )


@dataclass(frozen=True, slots=True)
class SelectionTimings:
    modifier_release_timeout_ms: int = 700
    modifier_poll_interval_ms: int = 15
    post_release_delay_ms: int = 45
    focus_restore_delay_ms: int = 35
    clipboard_timeout_ms: int = 900
    clipboard_poll_interval_ms: int = 25

    def __post_init__(self) -> None:
        for field_name in self.__dataclass_fields__:
            if getattr(self, field_name) < 1:
                raise ValueError(f"{field_name} must be positive")


class SelectionService(QObject):
    """Acquire selected text without blocking the Qt event loop."""

    started = Signal()
    text_ready = Signal(str, bool)
    failed = Signal(str)

    def __init__(
        self,
        clipboard_service: ClipboardService,
        *,
        input_backend: InputBackend | None = None,
        timings: SelectionTimings = SelectionTimings(),
    ) -> None:
        super().__init__()
        self._clipboard = clipboard_service
        self._input = input_backend or Win32InputBackend()
        self._timings = timings
        self._active = False
        self._snapshot: ClipboardSnapshot | None = None
        self._release_keys: tuple[int, ...] = ()
        self._modifier_started_at = 0.0
        self._clipboard_started_at = 0.0
        self._sequence_before_copy: int | None = None
        self._copy_sequence: int | None = None
        self._target: FocusTarget | None = None
        self._active_started_at = 0.0

        self._modifier_timer = QTimer(self)
        self._modifier_timer.setInterval(timings.modifier_poll_interval_ms)
        self._modifier_timer.timeout.connect(self._check_modifier_release)

        self._post_release_timer = QTimer(self)
        self._post_release_timer.setSingleShot(True)
        self._post_release_timer.setInterval(timings.post_release_delay_ms)
        self._post_release_timer.timeout.connect(self._send_copy)

        self._focus_restore_timer = QTimer(self)
        self._focus_restore_timer.setSingleShot(True)
        self._focus_restore_timer.setInterval(timings.focus_restore_delay_ms)
        self._focus_restore_timer.timeout.connect(self._send_copy_to_target)

        self._clipboard_timer = QTimer(self)
        self._clipboard_timer.setInterval(timings.clipboard_poll_interval_ms)
        self._clipboard_timer.timeout.connect(self._poll_clipboard)

    @property
    def is_active(self) -> bool:
        return self._active

    def capture_selected_text(self, release_keys: tuple[int, ...]) -> bool:
        """Copy the foreground selection after every hotkey key is released."""

        if self._active:
            logger.warning("clipboard_acquisition_failed reason=already_active")
            self.failed.emit("Selected-text capture is already in progress.")
            return False
        target = self._input.capture_target()
        try:
            snapshot = self._clipboard.capture_snapshot()
        except ClipboardError as exc:
            logger.warning(
                "clipboard_acquisition_failed reason=snapshot exception_type=%s",
                type(exc).__name__,
            )
            self.failed.emit(str(exc))
            return False

        self._active = True
        self._snapshot = snapshot
        self._target = target
        self._release_keys = tuple(release_keys)
        self._modifier_started_at = time.monotonic()
        self._active_started_at = self._modifier_started_at
        self._sequence_before_copy = snapshot.sequence_number
        self._copy_sequence = None
        self.started.emit()
        logger.info("clipboard_acquisition_started")
        self._modifier_timer.start()
        self._check_modifier_release()
        return True

    @Slot()
    def _check_modifier_release(self) -> None:
        if not self._active:
            return
        if all(not self._input.is_key_down(key) for key in self._release_keys):
            self._modifier_timer.stop()
            self._post_release_timer.start()
            return
        elapsed_ms = (time.monotonic() - self._modifier_started_at) * 1000
        if elapsed_ms >= self._timings.modifier_release_timeout_ms:
            self._fail("Release the translation hotkey and try again.")

    @Slot()
    def _send_copy(self) -> None:
        if not self._active:
            return
        if not self._input.restore_target(self._target):
            self._fail(
                "The target app lost keyboard focus before text could be copied. "
                "Select the text and try again."
            )
            return
        # WM_CANCELMODE is processed by the target application's event queue.
        # Give it a brief non-blocking interval before injecting Ctrl+C.
        self._focus_restore_timer.start()

    @Slot()
    def _send_copy_to_target(self) -> None:
        if not self._active:
            return
        self._sequence_before_copy = self._clipboard.sequence_number()
        if self._sequence_before_copy is None:
            self._fail(
                "Clipboard change tracking is unavailable. Try OCR mode with Alt+Q."
            )
            return
        if not self._input.send_ctrl_c():
            self._fail(
                "Could not send Ctrl+C to the target app. Try OCR mode with Alt+Q."
            )
            return
        self._clipboard_started_at = time.monotonic()
        self._clipboard_timer.start()

    @Slot()
    def _poll_clipboard(self) -> None:
        if not self._active or self._sequence_before_copy is None:
            return

        current_sequence = self._clipboard.sequence_number()
        if current_sequence is None:
            self._fail(
                "Clipboard change tracking became unavailable. Try OCR mode with Alt+Q."
            )
            return

        if current_sequence != self._sequence_before_copy:
            if self._copy_sequence is None:
                self._copy_sequence = current_sequence
            elif current_sequence != self._copy_sequence:
                self._fail(
                    "The clipboard changed again before LocalLens could restore it."
                )
                return

            selected_text = self._clipboard.read_text()
            sequence_after_read = self._clipboard.sequence_number()
            if sequence_after_read != self._copy_sequence:
                self._fail(
                    "The clipboard changed again before LocalLens could restore it."
                )
                return
            if selected_text is not None and selected_text.strip():
                snapshot = self._snapshot
                restored = bool(
                    snapshot is not None
                    and self._clipboard.restore_snapshot(
                        snapshot,
                        expected_sequence_number=self._copy_sequence,
                    )
                )
                self._complete()
                logger.info(
                    "clipboard_acquisition_succeeded duration_ms=%d restored=%s",
                    round((time.monotonic() - self._active_started_at) * 1000),
                    str(restored).lower(),
                )
                self.text_ready.emit(selected_text, restored)
                return

        elapsed_ms = (time.monotonic() - self._clipboard_started_at) * 1000
        if elapsed_ms >= self._timings.clipboard_timeout_ms:
            self._fail(NO_TEXT_MESSAGE, restore_if_safe=True)

    def cancel(self) -> None:
        if not self._active:
            return
        if self._snapshot is not None and self._copy_sequence is not None:
            self._clipboard.restore_snapshot(
                self._snapshot,
                expected_sequence_number=self._copy_sequence,
            )
        self._complete()
        logger.info("clipboard_acquisition_cancelled")

    def _fail(self, message: str, *, restore_if_safe: bool = False) -> None:
        if (
            restore_if_safe
            and self._snapshot is not None
            and self._copy_sequence is not None
        ):
            self._clipboard.restore_snapshot(
                self._snapshot,
                expected_sequence_number=self._copy_sequence,
            )
        duration_ms = round((time.monotonic() - self._active_started_at) * 1000)
        reason = "no_text" if message == NO_TEXT_MESSAGE else "capture_error"
        self._complete()
        logger.warning(
            "clipboard_acquisition_failed reason=%s duration_ms=%d",
            reason,
            duration_ms,
        )
        self.failed.emit(message)

    def _complete(self) -> None:
        self._modifier_timer.stop()
        self._post_release_timer.stop()
        self._focus_restore_timer.stop()
        self._clipboard_timer.stop()
        self._active = False
        self._snapshot = None
        self._release_keys = ()
        self._sequence_before_copy = None
        self._copy_sequence = None
        self._target = None
