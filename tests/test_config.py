import ipaddress
import json
from dataclasses import replace
from urllib.parse import urlparse

from locallens.config import (
    DEFAULT_SETTINGS,
    Settings,
    load_settings,
    resolve_project_root,
    save_settings,
)


def test_source_project_root_contains_the_application_package():
    assert (resolve_project_root(frozen=False) / "locallens").is_dir()


def test_frozen_project_root_is_the_executable_directory(tmp_path):
    executable = tmp_path / "portable" / "LocalLens.exe"

    assert resolve_project_root(frozen=True, executable=executable) == executable.parent


def test_default_ollama_endpoint_is_local_loopback():
    hostname = urlparse(DEFAULT_SETTINGS.ollama_url).hostname

    assert hostname is not None
    assert ipaddress.ip_address(hostname).is_loopback


def test_missing_config_creates_complete_defaults(tmp_path):
    config_path = tmp_path / "config" / "settings.json"

    settings = load_settings(config_path)

    assert settings == DEFAULT_SETTINGS
    assert settings.model == "deepseek-r1:8b"
    assert settings.max_chars_per_chunk == 3000
    assert settings.release_model_after_translation is True
    assert settings.start_with_windows is True
    assert json.loads(config_path.read_text(encoding="utf-8")) == settings.to_dict()


def test_valid_settings_round_trip(tmp_path):
    config_path = tmp_path / "settings.json"
    expected = replace(
        DEFAULT_SETTINGS,
        target_language="English",
        max_chars_per_chunk=4200,
        debug=True,
    )

    save_settings(expected, config_path)

    assert load_settings(config_path) == expected


def test_missing_fields_are_filled_and_persisted(tmp_path):
    config_path = tmp_path / "settings.json"
    config_path.write_text(
        json.dumps({"model": "deepseek-r1:8b"}), encoding="utf-8"
    )

    settings = load_settings(config_path)

    assert settings == DEFAULT_SETTINGS
    assert json.loads(config_path.read_text(encoding="utf-8")) == settings.to_dict()


def test_invalid_json_is_backed_up_before_defaults_are_restored(tmp_path):
    config_path = tmp_path / "settings.json"
    invalid_content = "{not valid json"
    config_path.write_text(invalid_content, encoding="utf-8")

    settings = load_settings(config_path)

    backups = list(tmp_path.glob("settings.json.corrupt-*"))
    assert settings == DEFAULT_SETTINGS
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == invalid_content
    assert json.loads(config_path.read_text(encoding="utf-8")) == settings.to_dict()


def test_legacy_translation_mode_is_removed_during_config_upgrade(tmp_path):
    config_path = tmp_path / "settings.json"
    invalid_settings = DEFAULT_SETTINGS.to_dict()
    invalid_settings["translation_mode"] = "fast"
    config_path.write_text(json.dumps(invalid_settings), encoding="utf-8")

    settings = load_settings(config_path)

    assert settings == DEFAULT_SETTINGS
    assert "translation_mode" not in json.loads(config_path.read_text(encoding="utf-8"))


def test_non_positive_chunk_size_recovers_to_default(tmp_path):
    config_path = tmp_path / "settings.json"
    invalid_settings = DEFAULT_SETTINGS.to_dict()
    invalid_settings["max_chars_per_chunk"] = 0
    config_path.write_text(json.dumps(invalid_settings), encoding="utf-8")

    settings = load_settings(config_path)

    assert settings.max_chars_per_chunk == 3000
    assert len(list(tmp_path.glob("settings.json.corrupt-*"))) == 1


def test_non_boolean_model_release_setting_recovers_to_default(tmp_path):
    config_path = tmp_path / "settings.json"
    invalid_settings = DEFAULT_SETTINGS.to_dict()
    invalid_settings["release_model_after_translation"] = "yes"
    config_path.write_text(json.dumps(invalid_settings), encoding="utf-8")

    settings = load_settings(config_path)

    assert settings.release_model_after_translation is True
    assert len(list(tmp_path.glob("settings.json.corrupt-*"))) == 1


def test_non_boolean_sign_in_startup_setting_recovers_to_default(tmp_path):
    config_path = tmp_path / "settings.json"
    invalid_settings = DEFAULT_SETTINGS.to_dict()
    invalid_settings["start_with_windows"] = "yes"
    config_path.write_text(json.dumps(invalid_settings), encoding="utf-8")

    settings = load_settings(config_path)

    assert settings.start_with_windows is True
    assert len(list(tmp_path.glob("settings.json.corrupt-*"))) == 1


def test_settings_reject_non_settings_save_input(tmp_path):
    config_path = tmp_path / "settings.json"

    try:
        save_settings({"debug": False}, config_path)  # type: ignore[arg-type]
    except TypeError as exc:
        assert "Settings" in str(exc)
    else:
        raise AssertionError("save_settings accepted an unvalidated mapping")


def test_settings_dataclass_accepts_the_current_defaults():
    assert Settings.from_mapping(DEFAULT_SETTINGS.to_dict()) == DEFAULT_SETTINGS
