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
"""

from __future__ import annotations

from typing import Any

from google import genai


class GeminiModel:
    """Drop-in replacement for the old `genai.GenerativeModel(model_name)`."""

    def __init__(self, *, api_key: str, model_name: str) -> None:
        self._client = genai.Client(api_key=api_key)
        self._model_name = model_name

    @property
    def model_name(self) -> str:
        return self._model_name

    def generate_content(self, contents: Any):
        return self._client.models.generate_content(model=self._model_name, contents=contents)


def get_model(*, api_key: str, model_name: str) -> GeminiModel:
    """Build a GeminiModel bound to one API key + model name."""
    return GeminiModel(api_key=api_key, model_name=model_name)


def get_client(*, api_key: str) -> "genai.Client":
    """Raw genai.Client, for callers that need something other than
    generate_content (e.g. embed_content)."""
    return genai.Client(api_key=api_key)
