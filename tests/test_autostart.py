import sys

from locallens import autostart


class _RegistryKey:
    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False


class _FakeRegistry:
    HKEY_CURRENT_USER = object()
    KEY_SET_VALUE = 2
    REG_SZ = 1

    def __init__(self):
        self.values = {}

    def CreateKeyEx(self, *_args):
        return _RegistryKey()

    def SetValueEx(self, _key, name, _reserved, _kind, value):
        self.values[name] = value

    def DeleteValue(self, _key, name):
        if name not in self.values:
            raise FileNotFoundError(name)
        del self.values[name]


def test_packaged_autostart_registers_background_command(monkeypatch, tmp_path):
    registry = _FakeRegistry()
    executable = tmp_path / "LocalLens.exe"
    monkeypatch.setattr(autostart, "winreg", registry)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))

    assert autostart.sync_autostart(True) is True
    assert registry.values["LocalLens"] == f'"{executable}" --background'


def test_packaged_autostart_removal_is_idempotent(monkeypatch):
    registry = _FakeRegistry()
    registry.values["LocalLens"] = "old"
    monkeypatch.setattr(autostart, "winreg", registry)
    monkeypatch.setattr(sys, "frozen", True, raising=False)

    assert autostart.sync_autostart(False) is True
    assert "LocalLens" not in registry.values
    assert autostart.sync_autostart(False) is True
