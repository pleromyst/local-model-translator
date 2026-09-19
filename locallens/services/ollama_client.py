"""Synchronous Ollama API client used by background workers in later stages."""

from __future__ import annotations

import logging
import time
from collections.abc import Mapping
from typing import Any

import requests

from locallens.config import Settings
from locallens.prompts import build_source_message, build_translation_system_prompt
from locallens.translation_policy import translation_target_for_source


RequestTimeout = float | tuple[float, float]
logger = logging.getLogger("locallens.ollama")


class OllamaError(RuntimeError):
    """Base class for expected Ollama failures."""


class OllamaConnectionError(OllamaError):
    """Ollama could not be reached."""


class OllamaTimeoutError(OllamaError):
    """Ollama did not answer before the configured request timeout."""


class OllamaModelNotFoundError(OllamaError):
    """The configured model is not installed in Ollama."""


class OllamaHTTPError(OllamaError):
    """Ollama returned an unsuccessful HTTP status."""


class OllamaResponseError(OllamaError):
    """Ollama returned malformed or incomplete response data."""


class OllamaClient:
    """Small stateless client for the Ollama tags, show, and chat endpoints."""

    def __init__(
        self,
        settings: Settings,
        *,
        session: requests.Session | None = None,
        timeout: RequestTimeout = (3.05, 120.0),
    ) -> None:
        if not isinstance(settings, Settings):
            raise TypeError("settings must be a Settings instance")
        self._settings = settings
        self._session = session or requests.Session()
        self._timeout = timeout

    @property
    def model(self) -> str:
        return self._settings.model

    def check_connection(self) -> bool:
        """Return whether a structurally valid tags response can be obtained."""

        try:
            self.list_models()
        except OllamaError:
            return False
        return True

    def list_models(self) -> list[str]:
        payload = self._request_json("GET", "/api/tags")
        raw_models = payload.get("models")
        if not isinstance(raw_models, list):
            raise OllamaResponseError("Ollama returned an invalid model list.")

        model_names: list[str] = []
        for raw_model in raw_models:
            if not isinstance(raw_model, Mapping):
                raise OllamaResponseError("Ollama returned an invalid model entry.")
            name = raw_model.get("name")
            if not isinstance(name, str) or not name.strip():
                raise OllamaResponseError("Ollama returned a model without a name.")
            model_names.append(name.strip())
        return model_names

    def get_model_info(self, model: str | None = None) -> dict[str, Any]:
        model_name = (model or self._settings.model).strip()
        if not model_name:
            raise ValueError("model must not be empty")
        payload = self._request_json(
            "POST",
            "/api/show",
            json_body={"model": model_name},
            model_name=model_name,
        )
        return dict(payload)

    def translate(self, text: str) -> str:
        """Translate one source with normal model thinking enabled."""

        target_language = translation_target_for_source(
            text,
            fallback=self._settings.target_language,
        )
        if target_language is None:
            return text
        source_text = text.strip("\r\n")

        messages = [
            {
                "role": "system",
                "content": build_translation_system_prompt(
                    target_language
                ),
            },
            {"role": "user", "content": build_source_message(source_text)},
        ]
        payload = self._request_json(
            "POST",
            "/api/chat",
            json_body={
                "model": self._settings.model,
                "messages": messages,
                "stream": False,
                "think": True,
                "keep_alive": (
                    0
                    if self._settings.release_model_after_translation
                    else self._settings.ollama_keep_alive
                ),
                "options": {"temperature": 0},
            },
            model_name=self._settings.model,
        )
        logger.info(
            "ollama_metrics thinking=true total_duration_ms=%s load_duration_ms=%s "
            "prompt_eval_count=%s eval_count=%s",
            self._duration_ms(payload.get("total_duration")),
            self._duration_ms(payload.get("load_duration")),
            self._count_metric(payload.get("prompt_eval_count")),
            self._count_metric(payload.get("eval_count")),
        )

        message = payload.get("message")
        if not isinstance(message, Mapping):
            raise OllamaResponseError("Ollama response did not contain a message.")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise OllamaResponseError(
                "Ollama returned an empty final translation."
            )
        translation = content.strip()
        if "<SOURCE_TEXT>" in translation or "</SOURCE_TEXT>" in translation:
            raise OllamaResponseError(
                "Ollama returned prompt control text instead of a clean translation."
            )
        return translation

    @staticmethod
    def _duration_ms(value: Any) -> str:
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            return "unavailable"
        return str(round(value / 1_000_000))

    @staticmethod
    def _count_metric(value: Any) -> str:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            return "unavailable"
        return str(value)

    def close(self) -> None:
        self._session.close()

    def __enter__(self) -> "OllamaClient":
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def _request_json(
        self,
        method: str,
        endpoint: str,
        *,
        json_body: Mapping[str, Any] | None = None,
        model_name: str | None = None,
    ) -> Mapping[str, Any]:
        url = f"{self._settings.ollama_url}{endpoint}"
        started_at = time.monotonic()
        try:
            response = self._session.request(
                method,
                url,
                json=dict(json_body) if json_body is not None else None,
                timeout=self._timeout,
            )
        except requests.Timeout as exc:
            logger.warning(
                "ollama_request timeout=true endpoint=%s duration_ms=%d model=%s",
                endpoint,
                round((time.monotonic() - started_at) * 1000),
                model_name or "none",
            )
            raise OllamaTimeoutError("The Ollama request timed out.") from exc
        except requests.ConnectionError as exc:
            logger.warning(
                "ollama_request connection_failed=true endpoint=%s duration_ms=%d "
                "exception_type=%s",
                endpoint,
                round((time.monotonic() - started_at) * 1000),
                type(exc).__name__,
            )
            raise OllamaConnectionError(
                "Could not connect to Ollama. Make sure Ollama is running."
            ) from exc
        except requests.RequestException as exc:
            logger.error(
                "ollama_request failed=true endpoint=%s duration_ms=%d "
                "exception_type=%s",
                endpoint,
                round((time.monotonic() - started_at) * 1000),
                type(exc).__name__,
            )
            raise OllamaConnectionError("The Ollama request failed.") from exc

        duration_ms = round((time.monotonic() - started_at) * 1000)
        logger.info(
            "ollama_request endpoint=%s duration_ms=%d http_status=%d model=%s",
            endpoint,
            duration_ms,
            response.status_code,
            model_name or "none",
        )

        if response.status_code == 404 and model_name is not None:
            logger.warning("ollama_model_not_found model=%s", model_name)
            raise OllamaModelNotFoundError(
                f"The Ollama model '{model_name}' was not found."
            )
        if not 200 <= response.status_code < 300:
            raise OllamaHTTPError(
                f"Ollama returned HTTP {response.status_code}."
            )

        try:
            payload = response.json()
        except (requests.JSONDecodeError, ValueError) as exc:
            logger.warning(
                "ollama_invalid_json endpoint=%s exception_type=%s",
                endpoint,
                type(exc).__name__,
            )
            raise OllamaResponseError("Ollama returned invalid JSON.") from exc
        if not isinstance(payload, Mapping):
            logger.warning("ollama_invalid_response endpoint=%s", endpoint)
            raise OllamaResponseError("Ollama returned an invalid JSON object.")
        return payload
