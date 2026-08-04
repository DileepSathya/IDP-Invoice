"""Text embedding providers for invoice vector search."""

from __future__ import annotations

import logging
import os
from datetime import datetime
from functools import lru_cache
from typing import Protocol, Sequence

from backend.app_paths import load_app_dotenv

logger = logging.getLogger(__name__)


class EmbeddingProvider(Protocol):
    @property
    def name(self) -> str: ...

    @property
    def dimension(self) -> int: ...

    def embed(self, texts: Sequence[str]) -> list[list[float]]: ...


class _FastEmbedProvider:
    def __init__(self, model_name: str) -> None:
        from fastembed import TextEmbedding

        self._model_name = model_name
        self._model = TextEmbedding(model_name=model_name)
        probe = next(self._model.embed(["dimension probe"]))
        self._dimension = len(probe)

    @property
    def name(self) -> str:
        return f"fastembed:{self._model_name}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        if not texts:
            return []
        return [list(vec) for vec in self._model.embed(list(texts))]


class _GeminiEmbedProvider:
    def __init__(self, model_name: str) -> None:
        from backend.agents.gemini_client import get_client

        load_app_dotenv()
        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is required for Gemini embeddings.")
        self._model_name = model_name
        self._client = get_client(api_key=api_key)
        probe = self._embed_one("dimension probe")
        self._dimension = len(probe)

    @property
    def name(self) -> str:
        return f"gemini:{self._model_name}"

    @property
    def dimension(self) -> int:
        return self._dimension

    def _embed_one(self, text: str) -> list[float]:
        from backend.agents.database import record_gemini_metrics
        from backend.agents.gemini_client import extract_usage_metadata

        model = self._model_name
        if not model.startswith("models/"):
            model = f"models/{model}"
        started_at = datetime.utcnow()
        try:
            result = self._client.models.embed_content(model=model, contents=text)
            ended_at = datetime.utcnow()
            record_gemini_metrics(
                {
                    "operation": "embed_content",
                    "source": "chat_embedding",
                    "model": self._model_name,
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "latency_seconds": (ended_at - started_at).total_seconds(),
                    "status": "success",
                    "usage": extract_usage_metadata(result),
                    "request_meta": {
                        "prompt_chars": len(text),
                        "response_chars": 0,
                    },
                }
            )
        except Exception as exc:
            ended_at = datetime.utcnow()
            record_gemini_metrics(
                {
                    "operation": "embed_content",
                    "source": "chat_embedding",
                    "model": self._model_name,
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "latency_seconds": (ended_at - started_at).total_seconds(),
                    "status": "error",
                    "usage": None,
                    "error_message": str(exc),
                    "request_meta": {
                        "prompt_chars": len(text),
                        "response_chars": 0,
                    },
                }
            )
            raise
        embeddings = getattr(result, "embeddings", None) or []
        values = embeddings[0].values if embeddings else None
        if not values:
            raise RuntimeError(f"Gemini embedding returned no vector for model {model}.")
        return [float(v) for v in values]

    def embed(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._embed_one(text or " ") for text in texts]


def _resolve_provider_name() -> str:
    load_app_dotenv()
    configured = (os.environ.get("CHAT_EMBEDDING_PROVIDER") or "auto").strip().lower()
    if configured in {"fastembed", "gemini"}:
        return configured
    if configured != "auto":
        logger.warning(
            "[Chat embeddings] Unknown CHAT_EMBEDDING_PROVIDER=%r; using auto.",
            configured,
        )
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    prefer_gemini = (os.environ.get("CHAT_EMBEDDING_PREFER_GEMINI") or "").strip().lower() in {
        "1",
        "true",
        "yes",
    }
    if prefer_gemini and api_key:
        return "gemini"
    return "fastembed"


@lru_cache(maxsize=1)
def get_embedding_provider() -> EmbeddingProvider:
    provider_name = _resolve_provider_name()
    if provider_name == "gemini":
        model = (os.environ.get("GEMINI_EMBEDDING_MODEL") or "text-embedding-004").strip()
        logger.info("[Chat embeddings] Using Gemini embeddings (%s).", model)
        return _GeminiEmbedProvider(model)

    model = (os.environ.get("CHAT_EMBEDDING_FASTEMBED_MODEL") or "BAAI/bge-small-en-v1.5").strip()
    logger.info("[Chat embeddings] Using local FastEmbed model (%s).", model)
    return _FastEmbedProvider(model)


def embed_texts(texts: Sequence[str]) -> list[list[float]]:
    return get_embedding_provider().embed(texts)
