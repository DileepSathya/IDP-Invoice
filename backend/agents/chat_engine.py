"""Offline-first invoice chat: Qdrant semantic search, structured intents, optional Gemini."""

from __future__ import annotations

import logging
import os
import re
from typing import Any

from backend.agents.chat_analytics import (
    answer_all_time_totals,
    answer_date_range_analytics,
    answer_field_search,
    answer_hitl_analytics,
    answer_recent_invoices,
    format_hitl_answer,
    format_recent_invoices_answer,
    format_semantic_search_answer,
    fetch_recent_invoices,
    hitl_pending_only,
    parse_date_range,
    parse_field_filter,
    rollup_hitl_invoices,
    wants_hitl_query,
    wants_invoice_list,
    wants_recent_query,
    wants_table_display,
)
from backend.agents.chat_sessions import append_message, get_or_create_session, list_user_questions
from backend.agents.chat_vector_index import search as vector_search
from backend.agents.rag_chatbot import RetrievedChunk, answer_question
from backend.app_logging import configure_logging

logger = logging.getLogger(__name__)

DEFAULT_SUGGESTIONS = [
    "How many invoices were processed today?",
    "How many invoices in the past 2 days and what's their sum?",
    "Show invoices pending HITL review",
    "What are the most recent invoices?",
]

_TOTAL_ALL_RE = re.compile(
    r"(total|sum).{0,30}(amount|value|invoice).{0,20}(all|database|stored|entire)",
    re.IGNORECASE,
)
_HISTORY_RE = re.compile(
    r"(what did i ask|my questions|questions i asked|conversation history|what have i asked)",
    re.IGNORECASE,
)


def get_suggestions() -> list[str]:
    return list(DEFAULT_SUGGESTIONS)


def _prior_user_questions(session: dict[str, Any], current: str) -> list[str]:
    questions = list_user_questions(session)
    current = current.strip()
    if questions and questions[-1] == current:
        questions = questions[:-1]
    return questions


def _last_assistant_content(session: dict[str, Any]) -> str | None:
    messages = session.get("messages") or []
    if not isinstance(messages, list):
        return None
    for msg in reversed(messages):
        if isinstance(msg, dict) and msg.get("role") == "assistant":
            content = str(msg.get("content") or "").strip()
            if content:
                return content
    return None


def _resolve_followup_intent(question: str, session: dict[str, Any]) -> str | None:
    if not (wants_table_display(question) or wants_invoice_list(question)):
        return None
    if wants_hitl_query(question) or parse_date_range(question) or wants_recent_query(question):
        return None
    if parse_field_filter(question):
        return None

    for prior_question in reversed(_prior_user_questions(session, question)[-5:]):
        if wants_hitl_query(prior_question):
            return "hitl"
        if parse_date_range(prior_question):
            return "date_range"
        if wants_recent_query(prior_question):
            return "recent"
        if parse_field_filter(prior_question):
            return "field_search"

    last_answer = _last_assistant_content(session)
    if not last_answer:
        return None
    if "pending HITL review" in last_answer or "HITL flagged" in last_answer:
        return "hitl"
    if "Invoices processed in" in last_answer:
        return "date_range"
    if "most recent invoices" in last_answer:
        return "recent"
    if "where seller matches" in last_answer or "where buyer matches" in last_answer:
        return "field_search"
    return None


def _answer_hitl_followup(question: str, session: dict[str, Any]) -> str:
    prior = _prior_user_questions(session, question)
    context_question = prior[-1] if prior else question
    pending_only = hitl_pending_only(context_question)
    rollup = rollup_hitl_invoices(pending_only=pending_only)
    return format_hitl_answer(rollup, pending_only=pending_only, as_table=True)


def _answer_recent_followup() -> str:
    rows = fetch_recent_invoices()
    return format_recent_invoices_answer(rows, limit=len(rows), as_table=True)


def _answer_field_search_followup(question: str, session: dict[str, Any]) -> str | None:
    prior = _prior_user_questions(session, question)
    context_question = prior[-1] if prior else question
    return answer_field_search(context_question)


def _format_history_answer(session: dict[str, Any]) -> str:
    questions = list_user_questions(session)
    if not questions:
        return "You have not asked any questions in this session yet."
    lines = ["Questions you asked in this session:\n"]
    for idx, q in enumerate(questions, start=1):
        lines.append(f"{idx}. {q}")
    return "\n".join(lines)


def _format_offline_rag_answer(question: str, chunks: list[RetrievedChunk]) -> str:
    source_ids = [chunk.source_id for chunk in chunks if chunk.source_id]
    return format_semantic_search_answer(question, source_ids)


def _try_structured_intent(question: str, session: dict[str, Any]) -> str | None:
    range_answer = answer_date_range_analytics(question)
    if range_answer is not None:
        return range_answer

    hitl_answer = answer_hitl_analytics(question)
    if hitl_answer is not None:
        return hitl_answer

    followup = _resolve_followup_intent(question, session)
    if followup == "hitl":
        return _answer_hitl_followup(question, session)
    if followup == "recent":
        return _answer_recent_followup()
    if followup == "field_search":
        followup_answer = _answer_field_search_followup(question, session)
        if followup_answer is not None:
            return followup_answer

    field_answer = answer_field_search(question)
    if field_answer is not None:
        return field_answer

    recent_answer = answer_recent_invoices(question)
    if recent_answer is not None:
        return recent_answer
    if _TOTAL_ALL_RE.search(question):
        return answer_all_time_totals()
    if _HISTORY_RE.search(question):
        return _format_history_answer(session)
    return None


def _gemini_available() -> bool:
    load_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY")
    offline_only = str(os.environ.get("CHAT_OFFLINE_ONLY", "")).strip().lower() in {"1", "true", "yes"}
    return bool(load_key) and not offline_only


def chat(
    question: str,
    *,
    session_id: str | None = None,
) -> dict[str, Any]:
    configure_logging()
    q = question.strip()
    if not q:
        raise ValueError("Question must not be empty.")

    session = get_or_create_session(session_id)
    sid = str(session["session_id"])
    append_message(sid, role="user", content=q)
    session = get_or_create_session(sid)

    structured = _try_structured_intent(q, session)
    if structured is not None:
        append_message(sid, role="assistant", content=structured)
        session = get_or_create_session(sid)
        return {
            "answer": structured,
            "session_id": sid,
            "messages": session.get("messages", []),
            "suggestions": get_suggestions(),
            "mode": "structured",
        }

    chunks = vector_search(q)
    if _gemini_available():
        try:
            logger.info("[Chat engine] Using Gemini for answer (online mode).")
            answer = answer_question(q)
            mode = "gemini"
        except Exception as exc:
            logger.warning("[Chat engine] Gemini failed, falling back to offline: %s", exc)
            answer = _format_offline_rag_answer(q, chunks)
            mode = "offline_fallback"
    else:
        answer = _format_offline_rag_answer(q, chunks)
        mode = "offline"

    append_message(sid, role="assistant", content=answer)
    session = get_or_create_session(sid)
    return {
        "answer": answer,
        "session_id": sid,
        "messages": session.get("messages", []),
        "suggestions": get_suggestions(),
        "mode": mode,
    }
