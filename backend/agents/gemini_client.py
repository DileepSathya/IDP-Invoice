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

Each successful or failed `generate_content` call is logged to MongoDB
(`IDP.Gemini_metrics`) when callers pass optional `metrics_context`.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from google import genai


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

    def __init__(self, *, api_key: str, model_name: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate_content(self, contents: Any, *, metrics_context: dict[str, Any] | None = None):
        started_at = datetime.utcnow()
        try:
            response = self._client.models.generate_content(
                model=self._model_name,
                contents=contents,
            )
        except Exception as exc:
            ended_at = datetime.utcnow()
            _persist_generate_content_metrics(
                model=self._model_name,
                contents=contents,
                started_at=started_at,
                ended_at=ended_at,
                status="error",
                response=None,
                metrics_context=metrics_context,
                error_message=str(exc),
            )
            raise
        ended_at = datetime.utcnow()
        _persist_generate_content_metrics(
            model=self._model_name,
            contents=contents,
            started_at=started_at,
            ended_at=ended_at,
            status="success",
            response=response,
            metrics_context=metrics_context,
        )
        return response


def get_model(*, api_key: str, model_name: str) -> GeminiModel:
    """Build a GeminiModel bound to one API key + model name."""
    return GeminiModel(api_key=api_key, model_name=model_name)


def get_client(*, api_key: str) -> "genai.Client":
    """Raw genai.Client, for callers that need something other than
    generate_content (e.g. embed_content)."""
    return genai.Client(api_key=api_key)
