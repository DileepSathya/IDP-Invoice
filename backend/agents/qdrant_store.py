"""Qdrant vector store for invoice semantic search."""

from __future__ import annotations

import json
import logging
import os
import uuid
from datetime import datetime
from functools import lru_cache
from typing import Any

from backend.agents.chat_embeddings import get_embedding_provider
from backend.agents.database import get_invoices_collection
from backend.agents.rag_chatbot import (
    RetrievedChunk,
    _build_search_text,
    _extract_gemini_json,
    _first,
    _format_context_chunk,
)
from backend.app_paths import load_app_dotenv, logs_dir

logger = logging.getLogger(__name__)

_META_PATH = logs_dir() / "chat_vector_index" / "qdrant_meta.json"
_DEFAULT_COLLECTION = "idp_invoices"
_BATCH_SIZE = 64


def _mongo_id_to_point_id(source_id: str) -> str:
    return str(uuid.uuid5(uuid.NAMESPACE_OID, source_id))


def _collection_name() -> str:
    load_app_dotenv()
    return (os.environ.get("QDRANT_COLLECTION") or _DEFAULT_COLLECTION).strip() or _DEFAULT_COLLECTION


def _qdrant_path() -> str:
    load_app_dotenv()
    raw = (os.environ.get("QDRANT_PATH") or "").strip()
    if raw:
        return raw
    return str(logs_dir() / "qdrant")


def _qdrant_url() -> str:
    load_app_dotenv()
    return (os.environ.get("QDRANT_URL") or "").strip()


@lru_cache(maxsize=1)
def _get_client():
    from qdrant_client import QdrantClient

    url = _qdrant_url()
    if url:
        api_key = (os.environ.get("QDRANT_API_KEY") or "").strip() or None
        logger.info("[Qdrant] Connecting to remote Qdrant at %s", url)
        return QdrantClient(url=url, api_key=api_key)

    path = _qdrant_path()
    logger.info("[Qdrant] Using local embedded store at %s", path)
    return QdrantClient(path=path)


def _load_meta() -> dict[str, Any] | None:
    if not _META_PATH.is_file():
        return None
    try:
        data = json.loads(_META_PATH.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception as exc:
        logger.warning("[Qdrant] Could not read index metadata: %s", exc)
        return None


def _save_meta(meta: dict[str, Any]) -> None:
    _META_PATH.parent.mkdir(parents=True, exist_ok=True)
    _META_PATH.write_text(json.dumps(meta, indent=2), encoding="utf-8")


def _invoice_count() -> int:
    try:
        return int(get_invoices_collection().count_documents({}))
    except Exception:
        return 0


def _index_is_current(*, force: bool) -> bool:
    if force:
        return False
    meta = _load_meta()
    if not meta:
        return False
    provider = get_embedding_provider()
    if meta.get("embedding_provider") != provider.name:
        return False
    if int(meta.get("vector_size", -1)) != provider.dimension:
        return False
    if int(meta.get("invoice_count", -1)) != _invoice_count():
        return False
    if meta.get("collection") != _collection_name():
        return False
    return True


def build_index(*, force: bool = False) -> int:
    """Embed every invoice and upsert into Qdrant."""
    if _index_is_current(force=force):
        meta = _load_meta() or {}
        return int(meta.get("chunk_count", 0))

    from qdrant_client.models import Distance, PointStruct, VectorParams

    provider = get_embedding_provider()
    coll = get_invoices_collection()
    docs = list(coll.find({}, sort=[("_id", -1)]))
    current_count = len(docs)

    logger.info(
        "[Qdrant] Building embedding index for %s invoice(s) using %s.",
        current_count,
        provider.name,
    )

    client = _get_client()
    collection = _collection_name()
    if client.collection_exists(collection):
        client.delete_collection(collection)
    client.create_collection(
        collection_name=collection,
        vectors_config=VectorParams(size=provider.dimension, distance=Distance.COSINE),
    )

    indexed = 0
    batch_docs: list[dict[str, Any]] = []
    batch_texts: list[str] = []

    def _flush_batch() -> None:
        nonlocal indexed, batch_docs, batch_texts
        if not batch_docs:
            return
        vectors = provider.embed(batch_texts)
        points = [
            PointStruct(
                id=_mongo_id_to_point_id(str(doc.get("_id"))),
                vector=vector,
                payload={
                    "source_id": str(doc.get("_id")),
                    "invoice_number": _first(
                        [
                            _extract_gemini_json(doc).get("invoice_number"),
                            _extract_gemini_json(doc).get("invoice"),
                        ]
                    ),
                    "context_text": _format_context_chunk(doc),
                    "search_text": _build_search_text(doc),
                },
            )
            for doc, vector in zip(batch_docs, vectors)
        ]
        client.upsert(collection_name=collection, points=points, wait=True)
        indexed += len(points)
        batch_docs = []
        batch_texts = []

    for doc in docs:
        batch_docs.append(doc)
        batch_texts.append(_build_search_text(doc))
        if len(batch_docs) >= _BATCH_SIZE:
            _flush_batch()
    _flush_batch()

    _save_meta(
        {
            "backend": "qdrant",
            "built_at": datetime.utcnow().isoformat() + "Z",
            "collection": collection,
            "invoice_count": current_count,
            "chunk_count": indexed,
            "embedding_provider": provider.name,
            "vector_size": provider.dimension,
            "full_database": True,
        }
    )
    logger.info("[Qdrant] Indexed %s invoice(s) into collection %r.", indexed, collection)
    return indexed


def search(question: str, *, top_k: int = 8) -> list[RetrievedChunk]:
    """Semantic search over embedded invoices in Qdrant."""
    build_index()
    provider = get_embedding_provider()
    query_vector = provider.embed([question or " "])[0]
    client = _get_client()
    collection = _collection_name()

    try:
        hits = client.search(
            collection_name=collection,
            query_vector=query_vector,
            limit=max(1, top_k),
            with_payload=True,
        )
    except Exception as exc:
        logger.warning("[Qdrant] Search failed: %s", exc)
        return []

    chunks: list[RetrievedChunk] = []
    for hit in hits:
        payload = hit.payload or {}
        chunks.append(
            RetrievedChunk(
                source_id=str(payload.get("source_id") or hit.id),
                invoice_number=str(payload.get("invoice_number") or ""),
                context_text=str(payload.get("context_text") or ""),
            )
        )

    logger.info(
        "[Qdrant] Semantic search returned %s match(es) (top_k=%s).",
        len(chunks),
        top_k,
    )
    return chunks
