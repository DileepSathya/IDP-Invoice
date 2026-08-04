"""Shared Gemini client construction (google-genai SDK).

google.generativeai (the old SDK) is deprecated/EOL and receives no further
updates or bug fixes. This project uses the unified `google-genai` package
instead (`from google import genai`). The new SDK dropped the old
`genai.configure(api_key=...)` + `genai.GenerativeModel(name)` pattern - the
only entry point is `genai.Client(api_key=...)`, and generation happens via
`client.models.generate_content(model=name, contents=...)`.

`GeminiModel` below is a thin drop-in replacement for the old
`genai.GenerativeModel(name)` object: it exposes `.generate_content(contents)`
returning the same response shape (`.text`, and
`.usage_metadata.prompt_token_count` / `.candidates_token_count` /
`.total_token_count`, which kept the same names in the new SDK). This means
every caller's `response.text` / `response.usage_metadata` line stays
unchanged - only how the client/model gets constructed changes.

When `GEMINI_FALLBACK_MODELS` is set, a failed primary-model call that looks
like a transient Gemini API error immediately retries the next model in the
list (no sleep between attempts).

Each successful or failed `generate_content` call is logged to MongoDB
(`IDP.Gemini_metrics`) when callers pass optional `metrics_context`.
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any, Optional

from google import genai

from backend.pipeline_errors import is_gemini_api_error

logger = logging.getLogger(__name__)

_AUTH_ERROR_MARKERS = (
    "api key not valid",
    "invalid api key",
    "permission denied",
)


def extract_usage_metadata(response: Any) -> dict[str, Optional[int]]:
    usage = getattr(response, "usage_metadata", None)
    if usage is None:
        return {
            "prompt_token_count": None,
            "candidates_token_count": None,
            "total_token_count": None,
            "thoughts_token_count": None,
            "cached_content_token_count": None,
        }
    return {
        "prompt_token_count": getattr(usage, "prompt_token_count", None),
        "candidates_token_count": getattr(usage, "candidates_token_count", None),
        "total_token_count": getattr(usage, "total_token_count", None),
        "thoughts_token_count": getattr(usage, "thoughts_token_count", None),
        "cached_content_token_count": getattr(usage, "cached_content_token_count", None),
    }


def parse_fallback_models() -> list[str]:
    """Read comma-separated fallback model names from GEMINI_FALLBACK_MODELS."""
    raw = os.environ.get("GEMINI_FALLBACK_MODELS", "")
    return [model.strip() for model in raw.split(",") if model.strip()]


def _should_try_fallback(exc: BaseException) -> bool:
    if not is_gemini_api_error(exc):
        return False
    message = str(exc).lower()
    return not any(marker in message for marker in _AUTH_ERROR_MARKERS)


def _persist_generate_content_metrics(
    *,
    model: str,
    contents: Any,
    started_at: datetime,
    ended_at: datetime,
    status: str,
    response: Any | None,
    metrics_context: dict[str, Any] | None,
    error_message: str | None = None,
) -> None:
    from backend.agents.database import record_gemini_metrics

    raw = ""
    if response is not None:
        raw = (getattr(response, "text", None) or "").strip()
    usage = extract_usage_metadata(response) if response is not None else None
    ctx = dict(metrics_context or {})
    record_gemini_metrics(
        {
            **ctx,
            "operation": "generate_content",
            "model": model,
            "started_at": started_at,
            "ended_at": ended_at,
            "latency_seconds": (ended_at - started_at).total_seconds(),
            "status": status,
            "usage": usage,
            "error_message": error_message,
            "request_meta": {
                "prompt_chars": len(str(contents)),
                "response_chars": len(raw),
            },
        }
    )


class GeminiModel:
    """Drop-in replacement for the old `genai.GenerativeModel(model_name)`."""

    def __init__(
        self,
        *,
        api_key: str,
        model_name: str,
        fallback_models: list[str] | None = None,
    ) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model_name = model_name
        self._fallback_models = fallback_models or []

    @property
    def model_name(self) -> str:
        return self._model_name

    def _models_to_try(self) -> list[str]:
        models = [self._model_name]
        for candidate in self._fallback_models:
            if candidate not in models:
                models.append(candidate)
        return models

    def generate_content(self, contents: Any, *, metrics_context: dict[str, Any] | None = None):
        models_to_try = self._models_to_try()
        last_exc: Exception | None = None

        for idx, model in enumerate(models_to_try):
            started_at = datetime.utcnow()
            try:
                response = self._client.models.generate_content(
                    model=model,
                    contents=contents,
                )
            except Exception as exc:
                last_exc = exc
                ended_at = datetime.utcnow()
                _persist_generate_content_metrics(
                    model=model,
                    contents=contents,
                    started_at=started_at,
                    ended_at=ended_at,
                    status="error",
                    response=None,
                    metrics_context=metrics_context,
                    error_message=str(exc),
                )
                has_next = idx < len(models_to_try) - 1
                if has_next and _should_try_fallback(exc):
                    logger.warning(
                        "[Gemini] Model %s failed (%s); switching immediately to %s",
                        model,
                        exc,
                        models_to_try[idx + 1],
                    )
                    continue
                raise

            ended_at = datetime.utcnow()
            if idx > 0:
                logger.warning(
                    "[Gemini] Primary model failed; succeeded with fallback %s",
                    model,
                )
            self._model_name = model
            _persist_generate_content_metrics(
                model=model,
                contents=contents,
                started_at=started_at,
                ended_at=ended_at,
                status="success",
                response=response,
                metrics_context=metrics_context,
            )
            return response

        if last_exc is not None:
            raise last_exc
        raise RuntimeError("generate_content failed with no models configured")


def get_model(*, api_key: str, model_name: str) -> GeminiModel:
    """Build a GeminiModel bound to one API key + model name."""
    return GeminiModel(
        api_key=api_key,
        model_name=model_name,
        fallback_models=parse_fallback_models(),
    )


def get_client(*, api_key: str) -> "genai.Client":
    """Raw genai.Client, for callers that need something other than
    generate_content (e.g. embed_content)."""
    return genai.Client(api_key=api_key)
