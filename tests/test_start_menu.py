import sys

from locallens import start_menu


def test_packaged_start_menu_shortcut_targets_the_running_executable(
    monkeypatch, tmp_path
):
    executable = tmp_path / "LocalLens.exe"
    shortcut = tmp_path / "Programs" / "LocalLens.lnk"
    commands = []

    def run(command, **kwargs):
        commands.append((command, kwargs))

    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(executable))
    monkeypatch.setattr(start_menu, "_shortcut_path", lambda: shortcut)
    monkeypatch.setattr(start_menu.subprocess, "run", run)

    assert start_menu.ensure_start_menu_shortcut() is True
    script = commands[0][0][-1]
    assert str(shortcut) in script
    assert str(executable) in script
    assert commands[0][1]["check"] is True


def test_source_run_does_not_create_a_start_menu_shortcut(monkeypatch):
    monkeypatch.delattr(sys, "frozen", raising=False)

    assert start_menu.ensure_start_menu_shortcut() is False
