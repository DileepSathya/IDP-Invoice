"""Invoice vector search: Qdrant + embeddings (primary), local TF-IDF (fallback)."""

from __future__ import annotations

import json
import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from backend.agents.database import get_invoices_collection
from backend.agents.rag_chatbot import (
    RetrievedChunk,
    _build_search_text,
    _extract_gemini_json,
    _first,
    _format_context_chunk,
    _tokenize,
)
from backend.app_paths import load_app_dotenv, logs_dir

logger = logging.getLogger(__name__)

_INDEX_DIR = logs_dir() / "chat_vector_index"
_INDEX_META = _INDEX_DIR / "meta.json"
_INDEX_CHUNKS = _INDEX_DIR / "chunks.json"
_BATCH_SIZE = 500


def _use_qdrant() -> bool:
    load_app_dotenv()
    backend = (os.environ.get("CHAT_VECTOR_BACKEND") or "qdrant").strip().lower()
    return backend not in {"tfidf", "local", "offline"}


def _index_dir() -> Path:
    path = _INDEX_DIR
    path.mkdir(parents=True, exist_ok=True)
    return path


def _tokenize_doc(text: str) -> list[str]:
    return _tokenize(text)


def _compute_idf(doc_tokens: list[list[str]]) -> dict[str, float]:
    n = len(doc_tokens) or 1
    df: dict[str, int] = {}
    for tokens in doc_tokens:
        for term in set(tokens):
            df[term] = df.get(term, 0) + 1
    return {term: math.log((n + 1) / (count + 1)) + 1.0 for term, count in df.items()}


def _tfidf_vector(tokens: list[str], idf: dict[str, float]) -> dict[str, float]:
    if not tokens:
        return {}
    tf: dict[str, int] = {}
    for t in tokens:
        tf[t] = tf.get(t, 0) + 1
    denom = float(len(tokens))
    vec: dict[str, float] = {}
    for term, count in tf.items():
        weight = (count / denom) * idf.get(term, 0.0)
        if weight > 0:
            vec[term] = weight
    return vec


def _cosine_similarity(a: dict[str, float], b: dict[str, float]) -> float:
    if not a or not b:
        return 0.0
    dot = sum(a.get(k, 0.0) * b.get(k, 0.0) for k in set(a) | set(b))
    norm_a = math.sqrt(sum(v * v for v in a.values()))
    norm_b = math.sqrt(sum(v * v for v in b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


@dataclass(frozen=True)
class IndexedChunk:
    source_id: str
    invoice_number: str
    context_text: str
    search_text: str
    vector: dict[str, float]


def _load_tfidf_index() -> tuple[dict[str, Any], list[IndexedChunk], dict[str, float]] | None:
    if not _INDEX_META.is_file() or not _INDEX_CHUNKS.is_file():
        return None
    try:
        meta = json.loads(_INDEX_META.read_text(encoding="utf-8"))
        raw_idf = meta.get("idf") or {}
        idf = {str(k): float(v) for k, v in raw_idf.items()}
        raw_chunks = json.loads(_INDEX_CHUNKS.read_text(encoding="utf-8"))
        chunks: list[IndexedChunk] = []
        for row in raw_chunks:
            chunks.append(
                IndexedChunk(
                    source_id=str(row["source_id"]),
                    invoice_number=str(row.get("invoice_number") or ""),
                    context_text=str(row["context_text"]),
                    search_text=str(row["search_text"]),
                    vector={str(k): float(v) for k, v in (row.get("vector") or {}).items()},
                )
            )
        return meta, chunks, idf
    except Exception as exc:
        logger.warning("[Chat vector index] Could not load TF-IDF cache: %s", exc)
        return None


def _save_tfidf_index(meta: dict[str, Any], chunks: list[IndexedChunk], idf: dict[str, float]) -> None:
    _index_dir()
    meta = dict(meta)
    meta["idf"] = idf
    meta["backend"] = "tfidf"
    _INDEX_META.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    payload = [
        {
            "source_id": c.source_id,
            "invoice_number": c.invoice_number,
            "context_text": c.context_text,
            "search_text": c.search_text,
            "vector": c.vector,
        }
        for c in chunks
    ]
    _INDEX_CHUNKS.write_text(json.dumps(payload), encoding="utf-8")


def _invoice_count() -> int:
    try:
        return int(get_invoices_collection().count_documents({}))
    except Exception:
        return 0


def _build_tfidf_index(*, force: bool = False) -> int:
    current_count = _invoice_count()
    cached = None if force else _load_tfidf_index()
    if cached is not None:
        meta, _, _ = cached
        if (
            int(meta.get("invoice_count", -1)) == current_count
            and meta.get("full_database") is True
        ):
            return int(meta.get("chunk_count", 0))

    logger.info("[Chat vector index] Building TF-IDF fallback index from MongoDB.")
    coll = get_invoices_collection()
    cursor = coll.find({}, sort=[("_id", -1)], batch_size=_BATCH_SIZE)

    docs: list[dict[str, Any]] = list(cursor)
    doc_tokens = [_tokenize_doc(_build_search_text(doc)) for doc in docs]
    idf = _compute_idf(doc_tokens)

    chunks: list[IndexedChunk] = []
    for doc, tokens in zip(docs, doc_tokens):
        gemini_json = _extract_gemini_json(doc)
        invoice_number = _first(
            [gemini_json.get("invoice_number"), gemini_json.get("invoice")]
        )
        search_text = _build_search_text(doc)
        chunks.append(
            IndexedChunk(
                source_id=str(doc.get("_id")),
                invoice_number=invoice_number,
                context_text=_format_context_chunk(doc),
                search_text=search_text,
                vector=_tfidf_vector(tokens, idf),
            )
        )

    meta = {
        "built_at": datetime.utcnow().isoformat() + "Z",
        "invoice_count": current_count,
        "chunk_count": len(chunks),
        "full_database": True,
    }
    _save_tfidf_index(meta, chunks, idf)
    logger.info("[Chat vector index] TF-IDF index built for %s invoice(s).", len(chunks))
    return len(chunks)


def _search_tfidf(question: str, *, top_k: int = 8) -> list[RetrievedChunk]:
    _build_tfidf_index()
    loaded = _load_tfidf_index()
    if not loaded:
        return []

    _, chunks, idf = loaded
    if not chunks:
        return []

    query_vec = _tfidf_vector(_tokenize_doc(question), idf)
    scored = [(_cosine_similarity(query_vec, c.vector), c) for c in chunks]
    scored.sort(key=lambda x: x[0], reverse=True)

    selected = [c for score, c in scored[:top_k] if score > 0]
    if not selected:
        selected = [c for _, c in scored[:top_k]]

    logger.info(
        "[Chat vector index] TF-IDF search over %s invoice(s); returning %s match(es).",
        len(chunks),
        len(selected),
    )
    return [
        RetrievedChunk(
            source_id=c.source_id,
            invoice_number=c.invoice_number,
            context_text=c.context_text,
        )
        for c in selected
    ]


def build_index(*, force: bool = False) -> int:
    """Build or refresh the invoice vector index."""
    if _use_qdrant():
        try:
            from backend.agents import qdrant_store

            return qdrant_store.build_index(force=force)
        except Exception as exc:
            logger.warning(
                "[Chat vector index] Qdrant index build failed (%s); using TF-IDF fallback.",
                exc,
            )
    return _build_tfidf_index(force=force)


def search(question: str, *, top_k: int = 8) -> list[RetrievedChunk]:
    """Semantic search (Qdrant + embeddings) with TF-IDF fallback."""
    if _use_qdrant():
        try:
            from backend.agents import qdrant_store

            chunks = qdrant_store.search(question, top_k=top_k)
            if chunks:
                return chunks
        except Exception as exc:
            logger.warning(
                "[Chat vector index] Qdrant search failed (%s); using TF-IDF fallback.",
                exc,
            )
    return _search_tfidf(question, top_k=top_k)
