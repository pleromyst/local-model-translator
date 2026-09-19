"""Typed configuration loading, validation, recovery, and atomic saving."""

from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping
from urllib.parse import urlparse


def resolve_project_root(
    *,
    frozen: bool | None = None,
    executable: str | Path | None = None,
) -> Path:
    """Return the writable application directory in source and frozen builds."""

    is_frozen = bool(getattr(sys, "frozen", False)) if frozen is None else frozen
    if is_frozen:
        executable_path = Path(executable or sys.executable)
        return executable_path.resolve().parent
    return Path(__file__).resolve().parent.parent


PROJECT_ROOT = resolve_project_root()
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "settings.json"
logger = logging.getLogger("locallens.config")


class ConfigurationError(ValueError):
    """Raised when configuration data cannot be validated."""


def _non_empty_string(data: Mapping[str, Any], key: str) -> str:
    value = data.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{key} must be a non-empty string")
    return value.strip()


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated LocalLens settings."""

    ollama_url: str = "http://127.0.0.1:11434"
    model: str = "deepseek-r1:8b"
    target_language: str = "Simplified Chinese"
    translation_hotkey: str = "Alt+T"
    ocr_hotkey: str = "Alt+Q"
    max_chars_per_chunk: int = 3000
    ollama_keep_alive: str = "10m"
    release_model_after_translation: bool = True
    start_with_windows: bool = True
    debug: bool = False

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any]) -> "Settings":
        if not isinstance(data, Mapping):
            raise ConfigurationError("the configuration root must be an object")

        ollama_url = _non_empty_string(data, "ollama_url").rstrip("/")
        parsed_url = urlparse(ollama_url)
        try:
            port = parsed_url.port
        except ValueError as exc:
            raise ConfigurationError("ollama_url contains an invalid port") from exc
        if parsed_url.scheme not in {"http", "https"} or not parsed_url.hostname:
            raise ConfigurationError("ollama_url must be an HTTP or HTTPS URL")
        if port is not None and not 1 <= port <= 65535:
            raise ConfigurationError("ollama_url contains an invalid port")

        max_chars = data.get("max_chars_per_chunk")
        if isinstance(max_chars, bool) or not isinstance(max_chars, int) or max_chars <= 0:
            raise ConfigurationError("max_chars_per_chunk must be a positive integer")

        debug = data.get("debug")
        if not isinstance(debug, bool):
            raise ConfigurationError("debug must be a boolean")

        release_model = data.get("release_model_after_translation")
        if not isinstance(release_model, bool):
            raise ConfigurationError(
                "release_model_after_translation must be a boolean"
            )

        start_with_windows = data.get("start_with_windows")
        if not isinstance(start_with_windows, bool):
            raise ConfigurationError("start_with_windows must be a boolean")

        return cls(
            ollama_url=ollama_url,
            model=_non_empty_string(data, "model"),
            target_language=_non_empty_string(data, "target_language"),
            translation_hotkey=_non_empty_string(data, "translation_hotkey"),
            ocr_hotkey=_non_empty_string(data, "ocr_hotkey"),
            max_chars_per_chunk=max_chars,
            ollama_keep_alive=_non_empty_string(data, "ollama_keep_alive"),
            release_model_after_translation=release_model,
            start_with_windows=start_with_windows,
            debug=debug,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


DEFAULT_SETTINGS = Settings()


def save_settings(
    settings: Settings, path: str | Path = DEFAULT_CONFIG_PATH
) -> None:
    """Atomically persist validated settings as UTF-8 JSON."""

    if not isinstance(settings, Settings):
        raise TypeError("settings must be a Settings instance")

    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=config_path.parent,
            prefix=f".{config_path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            json.dump(
                settings.to_dict(), temporary_file, ensure_ascii=False, indent=2
            )
            temporary_file.write("\n")
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        os.replace(temporary_path, config_path)
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)


def _backup_corrupt_config(config_path: Path) -> Path:
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    backup_path = config_path.with_name(
        f"{config_path.name}.corrupt-{timestamp}"
    )
    config_path.replace(backup_path)
    return backup_path


def load_settings(path: str | Path = DEFAULT_CONFIG_PATH) -> Settings:
    """Load settings, filling missing fields and recovering invalid files.

    An invalid existing file is preserved beside the original with a
    ``.corrupt-<timestamp>`` suffix before a clean default file is written.
    """

    config_path = Path(path)
    if not config_path.exists():
        try:
            save_settings(DEFAULT_SETTINGS, config_path)
        except OSError as exc:
            logger.error(
                "config_default_write_failed exception_type=%s",
                type(exc).__name__,
            )
        else:
            logger.info("config_default_created")
        return DEFAULT_SETTINGS

    try:
        raw_data = json.loads(config_path.read_text(encoding="utf-8"))
        if not isinstance(raw_data, Mapping):
            raise ConfigurationError("the configuration root must be an object")
        merged_data = {**DEFAULT_SETTINGS.to_dict(), **raw_data}
        settings = Settings.from_mapping(merged_data)
    except (
        ConfigurationError,
        json.JSONDecodeError,
        UnicodeDecodeError,
        OSError,
    ) as exc:
        backup_created = False
        try:
            if config_path.exists():
                _backup_corrupt_config(config_path)
                backup_created = True
        except OSError:
            pass
        try:
            save_settings(DEFAULT_SETTINGS, config_path)
        except OSError:
            pass
        logger.warning(
            "config_invalid defaults_restored=true backup_created=%s "
            "exception_type=%s",
            str(backup_created).lower(),
            type(exc).__name__,
        )
        return DEFAULT_SETTINGS

    if raw_data != settings.to_dict():
        try:
            save_settings(settings, config_path)
        except OSError as exc:
            logger.error(
                "config_upgrade_write_failed exception_type=%s",
                type(exc).__name__,
            )
        else:
            logger.info("config_upgraded")
    return settings
