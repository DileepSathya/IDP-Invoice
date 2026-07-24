from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from backend.app_paths import load_app_dotenv
from pymongo import MongoClient

# Gemini / LLM
from backend.agents.gemini_client import get_model

from backend.agents.database import get_invoices_collection
from backend.app_logging import configure_logging


logger = logging.getLogger(__name__)


def _tokenize(text: str) -> list[str]:
    # Light-weight tokenizer for keyword scoring/retrieval.
    # Keeps numbers (invoice numbers/HSN) and words, drops very short tokens.
    tokens = re.findall(r"[A-Za-z0-9]+", text.lower())
    return [t for t in tokens if len(t) >= 3]


def _safe_str(value: Any) -> str:
    if value is None:
        return ""
    return str(value)


def _first(items: Sequence[Optional[Any]], fallback: str = "") -> str:
    for v in items:
        if v is None:
            continue
        s = str(v).strip()
        if s:
            return s
    return fallback


def _extract_gemini_json(doc: dict[str, Any]) -> dict[str, Any]:
    return (doc.get("gemini") or {}).get("json") or {}


def _extract_date_text(gemini_json: dict[str, Any]) -> tuple[str, str]:
    invoice_date_raw = _first([gemini_json.get("invoice_date"), gemini_json.get("date")])
    due_date_raw = _first([gemini_json.get("due_date"), gemini_json.get("dueDate")])
    return invoice_date_raw, due_date_raw


def _extract_party_text(gemini_json: dict[str, Any]) -> tuple[str, str]:
    seller = _first([gemini_json.get("seller")])
    buyer = _first([gemini_json.get("buyer")])
    return seller, buyer


def _extract_line_items(gemini_json: dict[str, Any]) -> list[dict[str, Any]]:
    line_items = gemini_json.get("line_items")
    if isinstance(line_items, list):
        return [li for li in line_items if isinstance(li, dict)]
    return []


def _score_document(question_tokens: list[str], search_text: str) -> int:
    if not question_tokens:
        return 0
    s = search_text.lower()
    return sum(1 for t in question_tokens if t in s)


def _build_search_text(doc: dict[str, Any]) -> str:
    gemini_json = _extract_gemini_json(doc)
    invoice_number = _first([gemini_json.get("invoice_number"), gemini_json.get("invoice")])
    invoice_date_raw, due_date_raw = _extract_date_text(gemini_json)
    seller, buyer = _extract_party_text(gemini_json)
    total_amount = _first([gemini_json.get("total_amount"), gemini_json.get("grand_total"), gemini_json.get("amount")])
    status_value = _first([gemini_json.get("status")], fallback="pending")
    payment_status_value = _first([gemini_json.get("payment_status")], fallback="not_paid")
    ocr_excerpt = _safe_str(doc.get("ocr_text", ""))[:3000]

    # Make sure we include line item fields for HSN/service-based questions.
    line_items = _extract_line_items(gemini_json)
    line_items_text_parts: list[str] = []
    for li in line_items[:15]:
        hsn = _first([li.get("hsn_number"), li.get("HSN_number"), li.get("HSN")])
        service = _first([li.get("service"), li.get("description"), li.get("Service")])
        qty = _first([li.get("quantity"), li.get("qnty"), li.get("qty")])
        amount_after_tax = _first([li.get("amount_after_tax"), li.get("amount_after_tax_value")])
        tax_rate = _first([li.get("tax_rate"), li.get("taxRate")])
        line_items_text_parts.append(f"{hsn} {service} {qty} {amount_after_tax} {tax_rate}")

    return "\n".join(
        [
            f"invoice_number: {invoice_number}",
            f"invoice_date: {invoice_date_raw}",
            f"due_date: {due_date_raw}",
            f"seller: {seller}",
            f"buyer: {buyer}",
            f"total_amount: {total_amount}",
            f"status: {status_value}",
            f"payment_status: {payment_status_value}",
            f"line_items: {' | '.join(line_items_text_parts)}",
            f"ocr_excerpt: {ocr_excerpt}",
        ]
    )


def _format_context_chunk(doc: dict[str, Any], max_line_items: int = 8, ocr_excerpt_chars: int = 600) -> str:
    gemini_json = _extract_gemini_json(doc)
    mongo_id = str(doc.get("_id"))

    invoice_number = _first([gemini_json.get("invoice_number"), gemini_json.get("invoice")])
    invoice_date_raw, due_date_raw = _extract_date_text(gemini_json)
    seller, buyer = _extract_party_text(gemini_json)
    total_amount = _first([gemini_json.get("total_amount"), gemini_json.get("grand_total"), gemini_json.get("amount")])
    status_value = _first([gemini_json.get("status")], fallback="pending")
    payment_status_value = _first([gemini_json.get("payment_status")], fallback="not_paid")

    line_items = _extract_line_items(gemini_json)

    line_items_lines: list[str] = []
    for li in line_items[:max_line_items]:
        hsn = _first([li.get("hsn_number"), li.get("HSN_number"), li.get("HSN")])
        service = _first([li.get("service"), li.get("description"), li.get("Service")])
        qty = _first([li.get("quantity"), li.get("qnty"), li.get("qty")])
        price = _first([li.get("price_per_unit"), li.get("price"), li.get("rate")])
        amount = _first([li.get("amount"), li.get("amount_pre_tax"), li.get("total")])
        tax_rate = _first([li.get("tax_rate"), li.get("taxRate")])
        tax_amount = _first([li.get("tax_amount"), li.get("taxAmount")])
        amount_after_tax = _first([li.get("amount_after_tax"), li.get("amount_after_tax_value")])
        line_items_lines.append(
            f"- HSN: {hsn or '—'} | Service: {service or '—'} | Qty: {qty or '—'} | "
            f"Amount: {amount or '—'} | Tax Rate: {tax_rate or '—'} | Tax Amt: {tax_amount or '—'} | "
            f"Total After Tax: {amount_after_tax or '—'} | Price/Unit: {price or '—'}"
        )

    ocr_excerpt = _safe_str(doc.get("ocr_text", ""))[:ocr_excerpt_chars].strip()

    line_items_block = line_items_lines if line_items_lines else ["(no line items)"]
    return "\n".join(
        [
            f"INVOICE SOURCE (mongo_id={mongo_id}):",
            f"invoice_number: {invoice_number or '—'}",
            f"invoice_date: {invoice_date_raw or '—'}",
            f"due_date: {due_date_raw or '—'}",
            f"seller: {seller or '—'}",
            f"buyer: {buyer or '—'}",
            f"total_amount: {total_amount or '—'}",
            f"status: {status_value or '—'}",
            f"payment_status: {payment_status_value or '—'}",
            "line_items:",
            *line_items_block,
            f"ocr_excerpt (for extra wording): {ocr_excerpt or '—'}",
        ]
    )


@dataclass(frozen=True)
class RetrievedChunk:
    source_id: str
    invoice_number: str
    context_text: str


def retrieve_relevant_chunks(
    question: str,
    *,
    candidate_limit: int = 200,
    top_k: int = 5,
) -> list[RetrievedChunk]:
    try:
        from backend.agents.chat_vector_index import search as vector_search

        logger.info(
            "[RAG chatbot] Step 1 — Qdrant semantic search with embeddings "
            "(backend.agents.chat_vector_index.search).",
        )
        return vector_search(question, top_k=top_k)
    except Exception as exc:
        logger.warning(
            "[RAG chatbot] Vector search unavailable (%s); falling back to keyword overlap.",
            exc,
        )

    logger.info(
        "[RAG chatbot] Step 1 — Loading invoices from MongoDB and scoring by keyword overlap "
        "(backend.agents.rag_chatbot.retrieve_relevant_chunks).",
    )
    coll = get_invoices_collection()
    question_tokens = _tokenize(question)

    cursor = coll.find({}, sort=[("_id", -1)], batch_size=500)

    scored: list[tuple[int, dict[str, Any]]] = []
    for doc in cursor:
        search_text = _build_search_text(doc)
        score = _score_document(question_tokens, search_text)
        if score > 0:
            scored.append((score, doc))

    # If nothing matched by keyword, fall back to the most recent docs.
    if not scored:
        logger.info(
            "[RAG chatbot] No keyword hits; using fallback: most recent %s invoice(s) as context.",
            min(candidate_limit, top_k),
        )
        fallback = coll.find({}, sort=[("_id", -1)], limit=min(candidate_limit, top_k))
        return [
            RetrievedChunk(
                source_id=str(doc.get("_id")),
                invoice_number=_first([_extract_gemini_json(doc).get("invoice_number"), _extract_gemini_json(doc).get("invoice")]),
                context_text=_format_context_chunk(doc),
            )
            for doc in fallback
        ]

    scored.sort(key=lambda x: x[0], reverse=True)
    selected = [d for _, d in scored[:top_k]]
    logger.info(
        "[RAG chatbot] Selected %s invoice document(s) as context chunks (top keyword scores).",
        len(selected),
    )

    chunks: list[RetrievedChunk] = []
    for doc in selected:
        gemini_json = _extract_gemini_json(doc)
        invoice_number = _first([gemini_json.get("invoice_number"), gemini_json.get("invoice")])
        chunks.append(
            RetrievedChunk(
                source_id=str(doc.get("_id")),
                invoice_number=invoice_number,
                context_text=_format_context_chunk(doc),
            )
        )
    return chunks


def _build_chat_prompt(question: str, chunks: list[RetrievedChunk]) -> str:
    context_text = "\n\n---\n\n".join(c.context_text for c in chunks)

    # Allow overriding the answer prompt via env.
    chat_prompt_template = os.environ.get("GEMINI_CHAT_PROMPT", "").strip()
    if chat_prompt_template and "{question}" in chat_prompt_template and "{context}" in chat_prompt_template:
        return chat_prompt_template.format(question=question, context=context_text)

    raise RuntimeError(
        "Missing/invalid GEMINI_CHAT_PROMPT in .env. "
        "It must include both placeholders: {question} and {context}."
    )


def _get_gemini_answer(prompt: str) -> str:
    load_app_dotenv()
    api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        raise RuntimeError("Missing GEMINI_API_KEY (or GOOGLE_API_KEY).")

    model_name = os.environ.get("GEMINI_MODEL") or "gemini-2.5-flash"
    logger.info(
        "[RAG chatbot] Step 3 — Calling Gemini (%s) with the built prompt (network call).",
        model_name,
    )
    model = get_model(api_key=api_key, model_name=model_name)
    response = model.generate_content(prompt)
    return str(response.text or "").strip()


def answer_question(question: str) -> str:
    configure_logging()
    chunks = retrieve_relevant_chunks(question)
    logger.info(
        "[RAG chatbot] Step 2 — Building prompt from GEMINI_CHAT_PROMPT template with {question} and {context}.",
    )
    prompt = _build_chat_prompt(question, chunks)
    return _get_gemini_answer(prompt)


def run_chatbot_cli() -> None:
    print("=== RAG Chatbot (MongoDB keyword retrieval + Gemini) ===")
    print("Type 'exit' to leave. Type 'refresh' to re-run retrieval each query.\n")
    while True:
        try:
            q = input("You> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not q:
            continue
        if q.lower() in {"exit", "quit"}:
            break
        if q.lower() == "refresh":
            print("[chatbot] Retrieval is stateless; next question will re-search MongoDB.")
            continue
        try:
            print()
            print("Bot>")
            print(answer_question(q))
            print()
        except Exception as e:
            print(f"[chatbot] Error: {e}")


if __name__ == "__main__":
    run_chatbot_cli()

