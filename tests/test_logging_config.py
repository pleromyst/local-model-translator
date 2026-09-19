import logging
from logging.handlers import RotatingFileHandler

import requests

from locallens.config import DEFAULT_SETTINGS, load_settings
from locallens.logging_config import (
    BACKUP_COUNT,
    MAX_LOG_BYTES,
    configure_logging,
    shutdown_logging,
)
from locallens.services.ollama_client import OllamaClient, OllamaTimeoutError


class FakeResponse:
    status_code = 200

    def __init__(self, content):
        self.content = content

    def json(self):
        return {
            "message": {
                "thinking": "PRIVATE_THINKING_DO_NOT_LOG",
                "content": self.content,
            },
            "total_duration": 2_500_000_000,
            "load_duration": 125_000_000,
            "prompt_eval_count": 48,
            "eval_count": 12,
        }


class FakeSession:
    def __init__(self, response):
        self.response = response

    def request(self, *args, **kwargs):
        if isinstance(self.response, Exception):
            raise self.response
        return self.response

    def close(self):
        pass


def read_log(path):
    for handler in logging.getLogger("locallens").handlers:
        handler.flush()
    return path.read_text(encoding="utf-8")


def test_rotating_private_log_has_bounded_size_and_backups(tmp_path):
    log_path = tmp_path / "logs" / "locallens.log"
    logger = configure_logging(path=log_path)
    logger.info("application_start version=test")

    handler = next(
        handler
        for handler in logger.handlers
        if isinstance(handler, RotatingFileHandler)
    )

    assert handler.maxBytes == MAX_LOG_BYTES
    assert handler.backupCount == BACKUP_COUNT
    assert "application_start version=test" in read_log(log_path)
    shutdown_logging()


def test_ollama_timing_log_never_contains_source_or_translation(tmp_path):
    log_path = tmp_path / "locallens.log"
    configure_logging(path=log_path)
    source = "PRIVATE_SOURCE_DO_NOT_LOG"
    translation = "PRIVATE_TRANSLATION_DO_NOT_LOG"
    client = OllamaClient(
        DEFAULT_SETTINGS,
        session=FakeSession(FakeResponse(translation)),  # type: ignore[arg-type]
    )

    assert client.translate(source) == translation
    content = read_log(log_path)

    assert "ollama_request" in content
    assert "duration_ms=" in content
    assert "http_status=200" in content
    assert "model=deepseek-r1:8b" in content
    assert "ollama_metrics thinking=true" in content
    assert "total_duration_ms=2500" in content
    assert "load_duration_ms=125" in content
    assert "prompt_eval_count=48" in content
    assert "eval_count=12" in content
    assert source not in content
    assert translation not in content
    assert "PRIVATE_THINKING_DO_NOT_LOG" not in content
    shutdown_logging()


def test_timeout_log_omits_private_exception_message(tmp_path):
    log_path = tmp_path / "locallens.log"
    configure_logging(path=log_path)
    private_message = "PRIVATE_TIMEOUT_DETAIL_DO_NOT_LOG"
    client = OllamaClient(
        DEFAULT_SETTINGS,
        session=FakeSession(requests.Timeout(private_message)),  # type: ignore[arg-type]
    )

    try:
        client.translate("PRIVATE_SOURCE_DO_NOT_LOG")
    except OllamaTimeoutError:
        pass
    else:
        raise AssertionError("timeout was not mapped")

    content = read_log(log_path)
    assert "timeout=true" in content
    assert private_message not in content
    assert "PRIVATE_SOURCE_DO_NOT_LOG" not in content
    shutdown_logging()


def test_corrupt_configuration_is_recovered_and_logged_without_contents(tmp_path):
    log_path = tmp_path / "locallens.log"
    config_path = tmp_path / "settings.json"
    private_invalid_content = "{PRIVATE_CONFIG_CONTENT_DO_NOT_LOG"
    config_path.write_text(private_invalid_content, encoding="utf-8")
    configure_logging(path=log_path)

    assert load_settings(config_path) == DEFAULT_SETTINGS
    content = read_log(log_path)

    assert "config_invalid" in content
    assert "defaults_restored=true" in content
    assert private_invalid_content not in content
    shutdown_logging()
