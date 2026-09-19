import ctypes
from ctypes import wintypes

import pytest

from locallens.hotkeys import (
    MOD_ALT,
    MOD_CONTROL,
    MOD_NOREPEAT,
    VK_CONTROL,
    VK_MENU,
    HotkeyManager,
    HotkeyRegistrationError,
    WM_HOTKEY,
    parse_hotkey,
)


class FakeApplication:
    def __init__(self):
        self.installed = []
        self.removed = []

    def installNativeEventFilter(self, event_filter):
        self.installed.append(event_filter)

    def removeNativeEventFilter(self, event_filter):
        self.removed.append(event_filter)


class FakeHotkeyBackend:
    def __init__(self, register_result=True, error=1409):
        self.register_result = register_result
        self.error = error
        self.registered = []
        self.unregistered = []

    def register_hotkey(self, window_id, hotkey_id, modifiers, virtual_key):
        self.registered.append((window_id, hotkey_id, modifiers, virtual_key))
        return self.register_result

    def unregister_hotkey(self, window_id, hotkey_id):
        self.unregistered.append((window_id, hotkey_id))
        return True

    def last_error(self):
        return self.error


def test_parse_default_translation_hotkey():
    spec = parse_hotkey("Alt+T")

    assert spec.display_name == "Alt+T"
    assert spec.modifiers == MOD_ALT | MOD_NOREPEAT
    assert spec.virtual_key == ord("T")
    assert spec.modifier_virtual_keys == (VK_MENU,)


def test_parse_hotkey_normalizes_aliases_and_function_keys():
    ctrl = parse_hotkey("control+f12")

    assert ctrl.display_name == "Ctrl+F12"
    assert ctrl.modifiers == MOD_CONTROL | MOD_NOREPEAT
    assert ctrl.virtual_key == 0x7B
    assert ctrl.modifier_virtual_keys == (VK_CONTROL,)


@pytest.mark.parametrize(
    "value",
    ["T", "Alt+", "Alt+T+Q", "Alt+Alt+T", "Alt+F25", "Alt+Space"],
)
def test_invalid_hotkeys_are_rejected(value):
    with pytest.raises(ValueError):
        parse_hotkey(value)


def test_manager_registers_dispatches_and_unregisters():
    backend = FakeHotkeyBackend()
    application = FakeApplication()
    manager = HotkeyManager(
        1234, backend=backend, application=application  # type: ignore[arg-type]
    )
    activated = []
    manager.activated.connect(activated.append)

    spec = manager.register("Alt+T")
    hotkey_id = backend.registered[0][1]

    assert backend.registered[0] == (
        1234,
        hotkey_id,
        MOD_ALT | MOD_NOREPEAT,
        ord("T"),
    )
    assert application.installed == [manager]
    assert manager.dispatch_hotkey(hotkey_id) is True
    assert manager.dispatch_hotkey(999) is False
    assert activated == [spec]

    manager.unregister_all()
    assert backend.unregistered == [(1234, hotkey_id)]
    assert application.removed == [manager]


def test_registration_conflict_has_explicit_message():
    manager = HotkeyManager(
        1234,
        backend=FakeHotkeyBackend(register_result=False, error=1409),
        application=FakeApplication(),  # type: ignore[arg-type]
    )

    with pytest.raises(
        HotkeyRegistrationError,
        match=r"The hotkey Alt\+T is already in use.*1409",
    ):
        manager.register("Alt+T")


def test_invalid_configured_hotkey_becomes_user_facing_registration_error():
    manager = HotkeyManager(
        1234,
        backend=FakeHotkeyBackend(),
        application=FakeApplication(),  # type: ignore[arg-type]
    )

    with pytest.raises(HotkeyRegistrationError, match="is invalid"):
        manager.register("Alt+Space")


def test_same_native_hotkey_message_is_dispatched_only_once():
    backend = FakeHotkeyBackend()
    manager = HotkeyManager(
        1234,
        backend=backend,
        application=FakeApplication(),  # type: ignore[arg-type]
    )
    activated = []
    manager.activated.connect(activated.append)
    spec = manager.register("Alt+T")
    hotkey_id = backend.registered[0][1]
    message = wintypes.MSG()
    message.message = WM_HOTKEY
    message.wParam = hotkey_id
    message.time = 12345
    pointer = ctypes.addressof(message)

    manager.nativeEventFilter(b"windows_dispatcher_MSG", pointer)
    manager.nativeEventFilter(b"windows_generic_MSG", pointer)

    assert activated == [spec]

    message.time = 12346
    manager.nativeEventFilter(b"windows_dispatcher_MSG", pointer)
    assert activated == [spec, spec]
    manager.unregister_all()
