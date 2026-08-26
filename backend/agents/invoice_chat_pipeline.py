"""
Invoice Chat Pipeline — 3-Layer Engine
───────────────────────────────────────
Integrated into the backend so the existing /chat endpoint uses it.

Layer 1  →  Gemini understands messy user query → structured intent JSON
Layer 2  →  Python builds safe MongoDB aggregation from intent (no LLM injection)
Layer 3  →  Gemini formats raw MongoDB results into clean human-readable answer

Uses the backend's existing env loading and MongoDB connection.
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import datetime, timezone, timedelta
from typing import Any

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# GEMINI CLIENT (lazy-init so it doesn't break import if key is missing)
# ─────────────────────────────────────────────────────────────────────────────

_gemini_model = None


def _get_gemini():
    global _gemini_model
    if _gemini_model is None:
        from backend.agents.gemini_client import get_model

        api_key = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY") or ""
        if not api_key:
            raise RuntimeError("GEMINI_API_KEY is not set.")
        model_name = os.environ.get("GEMINI_MODEL", "gemini-2.5-flash")
        _gemini_model = get_model(api_key=api_key, model_name=model_name)
    return _gemini_model


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 1 — INTENT ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _build_intent_prompt() -> str:
    """Build intent system prompt with today's UTC date injected for relative date resolution."""
    today = datetime.now(timezone.utc)
    today_str       = today.strftime("%Y-%m-%d")
    yesterday_str   = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    week_start_str  = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    month_start_str = today.replace(day=1).strftime("%Y-%m-%d")

    return f"""You are an invoice database query interpreter.
Convert the user's natural language query into a STRICT JSON object — no markdown, no explanation.

CRITICAL RULES:
1. NEVER ask for clarification. Always pick the most reasonable interpretation and answer.
2. When a query is ambiguous, use these defaults:
   - "processed" alone (no HITL context) → status=null, hitl=null (show full breakdown)
   - "this month" / "current month" → set bill_month to the current month-year
   - "pending" / "waiting" → HITL pending (hitl=true, status=1)
   - "all" / "everything" → no filters
3. If the user says "both status 0 and 2" or "all processed" → set status=null, hitl=false

=== Today's Date (UTC) ===
Today      : {today_str}
Yesterday  : {yesterday_str}
This week  : {week_start_str} to {today_str}
This month : {month_start_str} to {today_str}

=== MongoDB Document Schema ===
- gemini.json.invoice_number        : string  e.g. "SSS/25-26/00832"
- gemini.json.invoice_date          : string  e.g. "31/01/2026"  (date printed on the invoice)
- gemini.json.seller                : string  e.g. "SURABHI INTEGRATED SERVICES"
- gemini.json.buyer                 : string
- gemini.json.total_amount          : string  (numeric value stored as string)
- gemini.json.line_items[].service  : string  e.g. "SECURITY GUARD"
- gemini.json.additional_fields.status       : int  0=auto_processed, 1=HITL_pending, 2=HITL_reviewed
- gemini.json.additional_fields.HITL         : bool
- gemini.json.additional_fields.bill_for_month : string  e.g. "January-2026"
- file_status                       : string  "healthy file" or "corrupted"
- created_at                        : ISODate  (when invoice entered the system)
- processed_at                      : ISODate  (when the pipeline finished processing the invoice)

=== Output Format — return ONLY this JSON, clarification_needed must always be null ===
{{
  "intent_type": "<count|sum|list|detail|average|trend|duplicate>",
  "filters": {{
    "seller": "<substring or null>",
    "buyer": "<substring or null>",
    "status": <0|1|2|null>,
    "hitl": <true|false|null>,
    "bill_month": "<e.g. January-2026 or null>",
    "invoice_number": "<exact string or null>",
    "amount_gt": <number or null>,
    "amount_lt": <number or null>,
    "service_type": "<substring or null>",
    "file_status": "<healthy file|corrupted|null>",
    "date_from": "<YYYY-MM-DD or null>",
    "date_to": "<YYYY-MM-DD or null>",
    "processed_date_from": "<YYYY-MM-DD or null>",
    "processed_date_to": "<YYYY-MM-DD or null>"
  }},
  "limit": <number default 50>,
  "clarification_needed": null
}}

=== Intent Type Rules ===
count   : how many, count
sum     : total amount, sum, how much money
list    : show me, list, which invoices, display
detail  : one specific invoice by number
average : average value
trend   : month wise, breakdown, over time
duplicate : duplicate invoices, same invoice number, repeated

=== Status Mapping ===
0 = auto-processed (system handled, no human needed)
1 = HITL pending (flagged for human review)
2 = HITL reviewed (human already reviewed)

"HITL" / "pending review" / "needs approval"  → hitl=true, status=1
"auto-processed" / "system processed"          → status=0, hitl=null
"human-reviewed" / "HITL done"                 → status=2, hitl=null
"processed" / "done" (generic, no date)        → status=null, hitl=null  (show full breakdown)
"all invoices" / no status mentioned           → status=null, hitl=null

=== Date Filter Rules ===
IMPORTANT: "today", "yesterday", "this week", "this month" always refer to the PROCESSING DATE
(processed_at field — when the pipeline finished), NOT the invoice_date printed on the invoice.
Use processed_date_from / processed_date_to for these. Use date_from / date_to only when the
user explicitly asks about the date written on the invoice.
- "today" / "processed today"    → processed_date_from={today_str}, processed_date_to={today_str}
- "yesterday"                     → processed_date_from={yesterday_str}, processed_date_to={yesterday_str}
- "this week"                     → processed_date_from={week_start_str}, processed_date_to={today_str}
- "this month" (as processing date) → processed_date_from={month_start_str}, processed_date_to={today_str}
- No date mentioned               → both null (do NOT assume any date)

=== Examples ===
"total amount"                        → sum, no filters
"how many invoices"                   → count, no filters
"how many invoices processed today"   → count, processed_date_from={today_str}, processed_date_to={today_str}
"how many were processed today"       → count, processed_date_from={today_str}, processed_date_to={today_str}
"how many were processed"             → count, status=null, hitl=null (no date filter — show breakdown)
"system processed count"              → count, status=0
"show surabhi january"                → list, seller="surabhi", bill_month="January"
"invoices pending HITL review"        → list, hitl=true, status=1
"invoices reviewed by human"          → list, status=2
"month wise breakdown"                → trend
"corrupted files"                     → list, file_status="corrupted"
"invoices above 50000"                → list, amount_gt=50000
"i want both status 0 and 2"          → count/list, status=null, hitl=false
"invoices from 1 jan 2025 to 1 jan 2026" → list, date_from="2025-01-01", date_to="2026-01-01"
"invoices processed this week"        → list, processed_date_from={week_start_str}, processed_date_to={today_str}
"invoices in 2026"                    → list, date_from="2026-01-01", date_to="2026-12-31"
"is there any duplicate invoices"     → duplicate, no filters
"show me repeated invoice numbers"    → duplicate, no filters
"""


# Keep a module-level alias for any code that imports INTENT_SYSTEM_PROMPT directly
INTENT_SYSTEM_PROMPT = _build_intent_prompt()


_RELEVANCE_PROMPT = (
    "You are a relevance classifier for an invoice management system.\n"
    "Relevant topics: invoices, billing, payments, amounts, vendors, buyers, sellers, GST,\n"
    "invoice numbers, bill months, processing status, HITL review, corrupted files.\n"
    "NOT relevant: general knowledge, math, geography, history, politics, science, sports,\n"
    "weather, jokes, coding help, or anything unrelated to invoice/billing data.\n\n"
    "Examples of NOT relevant:\n"
    "  'who is the prime minister of india' → NO\n"
    "  'what is 2+2' → NO\n"
    "  'tell me a joke' → NO\n"
    "Examples of relevant:\n"
    "  'show me all invoices' → YES\n"
    "  'how many pending invoices' → YES\n"
    "  'total amount this month' → YES\n\n"
    "Reply with ONLY one word: YES or NO\n\n"
    "User question: {query}"
)


def _is_invoice_related(user_query: str, *, session_id: str | None = None) -> bool:
    """Binary relevance gate — runs before the full intent pipeline."""
    metrics_context: dict[str, Any] = {"source": "chat_relevance"}
    if session_id:
        metrics_context["session_id"] = session_id
    try:
        response = _get_gemini().generate_content(
            _RELEVANCE_PROMPT.format(query=user_query),
            metrics_context=metrics_context,
        )
        answer = response.text.strip().upper()
        logger.info("[Pipeline] Relevance check for %r → %s", user_query, answer)
        return answer.startswith("Y")
    except Exception:
        return True  # fail open — let the pipeline handle it


def extract_intent(
    user_query: str,
    chat_history: list[dict],
    *,
    session_id: str | None = None,
) -> dict:
    """Layer 1: Gemini converts messy query → structured intent JSON."""
    metrics_context: dict[str, Any] = {"source": "chat_intent"}
    if session_id:
        metrics_context["session_id"] = session_id
    history_text = ""
    for turn in chat_history[-4:]:
        role = "User" if turn.get("role") == "user" else "Assistant"
        history_text += f"{role}: {turn.get('content', '')}\n"

    prompt = _build_intent_prompt() + "\n\n"
    if history_text:
        prompt += f"=== Recent Conversation ===\n{history_text}\n"
    prompt += f"=== Current Query ===\n{user_query}"

    response = _get_gemini().generate_content(prompt, metrics_context=metrics_context)
    raw = response.text.strip()

    # Strip markdown code fences
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        logger.warning("[Pipeline] Intent parse failed for query: %r — using list fallback", user_query)
        return {
            "intent_type": "list",
            "filters": {},
            "limit": 10,
            "clarification_needed": None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 2 — QUERY BUILDER + EXECUTOR
# ─────────────────────────────────────────────────────────────────────────────

def _build_match(filters: dict) -> tuple[dict, dict | None]:
    """
    Convert intent filters → (MongoDB $match dict, optional amount_filter).

    HITL rules (status codes in this system):
      0 = system processed automatically (no human needed)
      1 = HITL pending       ← needs human review
      2 = HITL processed     ← human has reviewed it
    """
    match: dict[str, Any] = {}
    amount_filter: dict | None = None

    if filters.get("seller"):
        match["gemini.json.seller"] = {"$regex": filters["seller"], "$options": "i"}

    if filters.get("buyer"):
        match["gemini.json.buyer"] = {"$regex": filters["buyer"], "$options": "i"}

    # Resolve hitl + status together to avoid conflicts.
    # hitl=true always wins and uses $or on HITL bool AND status=1.
    hitl_val = filters.get("hitl")
    status_val = filters.get("status")

    if hitl_val is True:
        # Pending HITL: match docs where HITL boolean is True OR status=1.
        # Using $or is robust — some older docs may have one but not the other.
        match["$or"] = [
            {"gemini.json.additional_fields.HITL": True},
            {"gemini.json.additional_fields.status": 1},
        ]
    elif hitl_val is False:
        # No HITL needed — system processed docs only
        match["gemini.json.additional_fields.status"] = 0
    elif status_val is not None:
        # Explicit numeric status filter (no hitl flag)
        match["gemini.json.additional_fields.status"] = int(status_val)

    if filters.get("bill_month"):
        match["gemini.json.additional_fields.bill_for_month"] = {
            "$regex": filters["bill_month"], "$options": "i"
        }

    if filters.get("invoice_number"):
        match["gemini.json.invoice_number"] = filters["invoice_number"]

    if filters.get("service_type"):
        match["gemini.json.line_items"] = {
            "$elemMatch": {
                "service": {"$regex": filters["service_type"], "$options": "i"}
            }
        }

    if filters.get("file_status"):
        match["file_status"] = {"$regex": filters["file_status"], "$options": "i"}

    # Processed-at date range filter (when the pipeline finished processing)
    pd_from = filters.get("processed_date_from")
    pd_to   = filters.get("processed_date_to")
    if pd_from or pd_to:
        date_cond: dict = {}
        if pd_from:
            date_cond["$gte"] = datetime.strptime(pd_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if pd_to:
            date_cond["$lte"] = datetime.strptime(pd_to, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
        # processed_at exists on docs stored after the fix; fall back to created_at for older docs
        existing_or = match.get("$or")
        processed_at_or = [
            {"processed_at": date_cond},
            {"processed_at": {"$exists": False}, "created_at": date_cond},
        ]
        if existing_or:
            # Combine with existing $or via $and
            match.pop("$or")
            match["$and"] = [{"$or": existing_or}, {"$or": processed_at_or}]
        else:
            match["$or"] = processed_at_or

    # Amount range filters (total_amount is stored as string, handled via $addFields)
    af: dict = {}
    if filters.get("amount_gt") is not None:
        af["$gt"] = float(filters["amount_gt"])
    if filters.get("amount_lt") is not None:
        af["$lt"] = float(filters["amount_lt"])
    if af:
        amount_filter = af

    logger.debug("[Pipeline] Built match: %s | amount_filter: %s", match, amount_filter)
    return match, amount_filter


def _count_breakdown(col, base_match: dict) -> dict:
    """
    Count invoices by status using the same full-document Python calculation
    as _get_hitl_pending_docs (and the dashboard). No projection — fetches
    complete docs so calculate_hitl_flag has every field it needs.
    """
    from backend.hitl_status import calculate_hitl_flag, calculate_status_from_hitl, to_bool

    system_processed = 0
    hitl_pending     = 0
    hitl_reviewed    = 0

    for doc in col.find(base_match):   # full doc, no projection
        gj = (doc.get("gemini") or {}).get("json") or {}
        if not isinstance(gj, dict):
            continue
        af = gj.get("additional_fields") or {}

        if to_bool(af.get("human_approved")):
            hitl_reviewed += 1
            continue

        # Read-only aggregate scan over every invoice - reuse the last real ERP match
        # result (from ingest/edit/Force Sync) instead of re-running ERP matching per document
        # per query, which would otherwise hammer ERP master lookups on every chat/analytics call.
        hitl_value      = calculate_hitl_flag(gj, run_erp_matching_now=False)
        human_processed = to_bool(af.get("human_processed") or af.get("ever_hitl_true"))
        status          = calculate_status_from_hitl(hitl_value=hitl_value, human_processed=human_processed)

        if status == 0:
            system_processed += 1
        elif status == 1:
            hitl_pending += 1
        elif status == 2:
            hitl_reviewed += 1

    total = system_processed + hitl_pending + hitl_reviewed
    return {
        "type": "breakdown",
        "total": total,
        "system_processed": system_processed,
        "hitl_pending": hitl_pending,
        "hitl_reviewed": hitl_reviewed,
    }


def _get_hitl_pending_docs(col) -> list[dict]:
    """
    Fetch HITL-pending invoices by recalculating the flag in Python — same logic
    as the dashboard. This is necessary because the stored HITL/status fields can
    be stale (the backend uses `except: pass` on sync failures, so some docs may
    have incorrect stored values while the dashboard calculates correctly on-the-fly).
    """
    from backend.hitl_status import calculate_hitl_flag, calculate_status_from_hitl, to_bool

    results = []
    for doc in col.find({}):
        gj = (doc.get("gemini") or {}).get("json") or {}
        if not isinstance(gj, dict):
            continue
        af = gj.get("additional_fields") or {}

        # Skip human-approved invoices
        if to_bool(af.get("human_approved")):
            continue

        # Read-only scan over every invoice - see _count_breakdown above for why this
        # reuses the cached ERP match result instead of re-running matching per document.
        hitl_value     = calculate_hitl_flag(gj, run_erp_matching_now=False)
        human_processed = to_bool(af.get("human_processed") or af.get("ever_hitl_true"))
        status_value   = calculate_status_from_hitl(hitl_value=hitl_value, human_processed=human_processed)

        if status_value == 1:   # HITL pending
            results.append({
                "invoice_number": gj.get("invoice_number") or gj.get("invoice"),
                "invoice_date":   gj.get("invoice_date") or gj.get("date"),
                "seller":         gj.get("seller"),
                "buyer":          gj.get("buyer"),
                "total_amount":   gj.get("total_amount") or gj.get("grand_total"),
                "status":         status_value,
                "hitl":           hitl_value,
                "hitl_reason":    af.get("hitl_reason") or _hitl_reason(gj, af),
            })
    return results


def _hitl_reason(gj: dict, af: dict) -> str:
    """Derive a human-readable HITL reason from invoice data."""
    from backend.hitl_status import _to_float
    reasons = []
    if af.get("deblurred_applied"):
        reasons.append("file readability confidence is low")
    main_total    = _to_float(gj.get("total_amount") or gj.get("grand_total"))
    summary_total = _to_float(af.get("summary_total_amount"))
    if main_total is not None and summary_total is not None:
        if abs(main_total - summary_total) > 0.01:
            reasons.append("total amount mismatch")
    return ", ".join(reasons) if reasons else "flagged for review"


def _parse_invoice_date(date_str: str | None):
    """
    Parse invoice date strings in any format used in the database:
      31/01/2026   DD/MM/YYYY
      1-Dec-25     D-Mon-YY
      06-JAN-2026  DD-MON-YYYY
      11.02.2026   DD.MM.YYYY
      2026-01-31   ISO
    Returns a datetime.date or None.
    """
    if not date_str:
        return None
    from datetime import date
    import re as _re
    s = str(date_str).strip()

    # Try common patterns explicitly (faster than dateutil and handles 2-digit years)
    patterns = [
        ("%d/%m/%Y",  r"^\d{1,2}/\d{1,2}/\d{4}$"),
        ("%d-%m-%Y",  r"^\d{1,2}-\d{1,2}-\d{4}$"),
        ("%d.%m.%Y",  r"^\d{1,2}\.\d{1,2}\.\d{4}$"),
        ("%Y-%m-%d",  r"^\d{4}-\d{2}-\d{2}$"),
        ("%d-%b-%y",  r"^\d{1,2}-[A-Za-z]{3}-\d{2}$"),   # 1-Dec-25
        ("%d-%b-%Y",  r"^\d{1,2}-[A-Za-z]{3}-\d{4}$"),   # 06-JAN-2026
        ("%d %b %Y",  r"^\d{1,2} [A-Za-z]{3} \d{4}$"),
        ("%d %B %Y",  r"^\d{1,2} [A-Za-z]+ \d{4}$"),
    ]
    from datetime import datetime as _dt
    for fmt, pat in patterns:
        if _re.match(pat, s, _re.IGNORECASE):
            try:
                return _dt.strptime(s, fmt).date()
            except ValueError:
                pass
    # Fallback: try dateutil if installed
    try:
        from dateutil import parser as _dup
        return _dup.parse(s, dayfirst=True).date()
    except Exception:
        return None


def _apply_date_filter(docs: list[dict], date_from, date_to) -> list[dict]:
    """Filter a list of invoice dicts by invoice_date."""
    from datetime import date as _date
    result = []
    for d in docs:
        raw = d.get("invoice_date") or d.get("date")
        parsed = _parse_invoice_date(raw)
        if parsed is None:
            continue  # skip undated docs when a date range is requested
        if date_from and parsed < date_from:
            continue
        if date_to and parsed > date_to:
            continue
        result.append(d)
    return result


def _parse_date_filter(filters: dict):
    """Return (date_from, date_to) as datetime.date objects or None."""
    from datetime import datetime as _dt
    def _p(s):
        if not s:
            return None
        try:
            return _dt.strptime(str(s), "%Y-%m-%d").date()
        except Exception:
            return _parse_invoice_date(s)
    return _p(filters.get("date_from")), _p(filters.get("date_to"))


def execute_query(intent: dict) -> dict:
    """Layer 2: Build + run MongoDB query from structured intent."""
    from backend.agents.database import get_invoices_collection
    col = get_invoices_collection()

    intent_type = intent.get("intent_type", "list")
    filters     = intent.get("filters") or {}
    limit       = int(intent.get("limit") or 10)

    # ── HITL queries: recalculate in Python to match dashboard behaviour exactly
    hitl_requested = filters.get("hitl") is True or (
        filters.get("status") is not None and int(filters.get("status")) == 1
    )
    if hitl_requested:
        docs = _get_hitl_pending_docs(col)
        # Apply any additional seller/buyer filters
        if filters.get("seller"):
            import re as _re
            pat = _re.compile(filters["seller"], _re.IGNORECASE)
            docs = [d for d in docs if d.get("seller") and pat.search(d["seller"])]
        if filters.get("buyer"):
            import re as _re
            pat = _re.compile(filters["buyer"], _re.IGNORECASE)
            docs = [d for d in docs if d.get("buyer") and pat.search(d["buyer"])]
        total = len(docs)
        if intent_type == "count":
            return {"type": "count", "value": total, "filters_applied": filters}
        if intent_type == "sum":
            from backend.hitl_status import _to_float
            s = sum(_to_float(d.get("total_amount")) or 0 for d in docs)
            return {"type": "sum", "total_amount": s, "invoice_count": total, "filters_applied": filters}
        # list / detail
        return {
            "type": "list",
            "data": docs[:limit],
            "showing": min(len(docs), limit),
            "total_matched": total,
            "filters_applied": filters,
        }

    match, amount_filter = _build_match(filters)

    # ── COUNT ─────────────────────────────────────────────────────────────────
    if intent_type == "count":
        # When no specific status filter → return a full status breakdown (with date filter applied).
        # _count_breakdown uses col.find(match) so the processed_at date filter in match is respected.
        no_status_filter = filters.get("status") is None and filters.get("hitl") is not True
        if no_status_filter and not amount_filter:
            return _count_breakdown(col, match)

        pipeline = [{"$match": match}]
        if amount_filter:
            pipeline += [
                {"$addFields": {"_amt": {"$toDouble": "$gemini.json.total_amount"}}},
                {"$match": {"_amt": amount_filter}},
            ]
        pipeline.append({"$count": "total"})
        result = list(col.aggregate(pipeline))
        return {
            "type": "count",
            "value": result[0]["total"] if result else 0,
            "filters_applied": filters,
        }

    # ── SUM ───────────────────────────────────────────────────────────────────
    elif intent_type == "sum":
        pipeline = [
            {"$match": match},
            {"$addFields": {"_amt": {"$toDouble": "$gemini.json.total_amount"}}},
        ]
        if amount_filter:
            pipeline.append({"$match": {"_amt": amount_filter}})
        pipeline.append({
            "$group": {
                "_id": None,
                "total_amount": {"$sum": "$_amt"},
                "invoice_count": {"$sum": 1},
            }
        })
        result = list(col.aggregate(pipeline))
        if result:
            return {
                "type": "sum",
                "total_amount": result[0]["total_amount"],
                "invoice_count": result[0]["invoice_count"],
                "filters_applied": filters,
            }
        return {"type": "sum", "total_amount": 0, "invoice_count": 0, "filters_applied": filters}

    # ── AVERAGE ───────────────────────────────────────────────────────────────
    elif intent_type == "average":
        pipeline = [
            {"$match": match},
            {"$addFields": {"_amt": {"$toDouble": "$gemini.json.total_amount"}}},
        ]
        if amount_filter:
            pipeline.append({"$match": {"_amt": amount_filter}})
        pipeline.append({
            "$group": {
                "_id": None,
                "avg_amount": {"$avg": "$_amt"},
                "invoice_count": {"$sum": 1},
            }
        })
        result = list(col.aggregate(pipeline))
        if result:
            return {
                "type": "average",
                "avg_amount": result[0]["avg_amount"],
                "invoice_count": result[0]["invoice_count"],
                "filters_applied": filters,
            }
        return {"type": "average", "avg_amount": 0, "invoice_count": 0, "filters_applied": filters}

    # ── TREND ─────────────────────────────────────────────────────────────────
    elif intent_type == "trend":
        pipeline = [
            {"$match": match},
            {"$addFields": {"_amt": {"$toDouble": "$gemini.json.total_amount"}}},
            {
                "$group": {
                    "_id": "$gemini.json.additional_fields.bill_for_month",
                    "total_amount": {"$sum": "$_amt"},
                    "invoice_count": {"$sum": 1},
                }
            },
            {"$sort": {"_id": 1}},
        ]
        result = list(col.aggregate(pipeline))
        return {"type": "trend", "data": result, "filters_applied": filters}

    # ── DETAIL (single invoice) ───────────────────────────────────────────────

    # ── DUPLICATE (invoice numbers that appear more than once) ──────────────
    elif intent_type == "duplicate":
        dup_pipeline = [
            {"$match": match},
            {"$group": {
                "_id": "$gemini.json.invoice_number",
                "count":         {"$sum": 1},
                "invoice_dates": {"$push": "$gemini.json.invoice_date"},
                "sellers":       {"$push": "$gemini.json.seller"},
                "total_amounts": {"$push": "$gemini.json.total_amount"},
            }},
            {"$match": {"count": {"$gt": 1}, "_id": {"$ne": None}}},
            {"$sort": {"count": -1}},
        ]
        duplicates = list(col.aggregate(dup_pipeline))
        total_duplicate_groups = len(duplicates)
        total_duplicate_docs   = sum(d["count"] for d in duplicates)
        return {
            "type": "duplicate",
            "duplicate_groups": total_duplicate_groups,
            "total_duplicate_docs": total_duplicate_docs,
            "data": duplicates,
            "filters_applied": filters,
        }

    elif intent_type == "detail":
        doc = col.find_one(match, {"_id": 0, "gemini.json": 1, "file_path": 1})
        return {"type": "detail", "data": doc, "filters_applied": filters}

    # ── LIST (default) ────────────────────────────────────────────────────────
    else:
        date_from, date_to = _parse_date_filter(filters)
        has_date_filter = date_from is not None or date_to is not None

        if has_date_filter:
            # Date filtering must be done in Python — dates are stored as mixed-format strings.
            # Fetch all matching docs (no sort/limit yet) and filter by parsed date.
            from backend.hitl_status import _to_float
            pipeline_all: list[dict] = [{"$match": match}]
            if amount_filter:
                pipeline_all += [
                    {"$addFields": {"_amt": {"$toDouble": "$gemini.json.total_amount"}}},
                    {"$match": {"_amt": amount_filter}},
                ]
            pipeline_all.append({
                "$project": {
                    "_id": 0,
                    "invoice_number": {"$ifNull": ["$gemini.json.invoice_number", "$gemini.json.invoice"]},
                    "invoice_date":   "$gemini.json.invoice_date",
                    "seller":         "$gemini.json.seller",
                    "buyer":          "$gemini.json.buyer",
                    "total_amount":   "$gemini.json.total_amount",
                    "status":         "$gemini.json.additional_fields.status",
                    "hitl":           "$gemini.json.additional_fields.HITL",
                }
            })
            all_docs = list(col.aggregate(pipeline_all))
            filtered = _apply_date_filter(all_docs, date_from, date_to)

            # Deduplicate by invoice_number
            seen: set = set()
            deduped = []
            for d in filtered:
                key = d.get("invoice_number") or str(d)
                if key not in seen:
                    seen.add(key)
                    deduped.append(d)

            total_matched = len(deduped)
            total_amount  = sum(_to_float(d.get("total_amount")) or 0 for d in deduped)
            return {
                "type": "date_range_list",
                "data": deduped[:limit],
                "showing": min(len(deduped), limit),
                "total_matched": total_matched,
                "total_amount": total_amount,
                "date_from": str(date_from) if date_from else None,
                "date_to":   str(date_to)   if date_to   else None,
                "filters_applied": filters,
            }

        pipeline: list[dict] = [{"$match": match}]
        if amount_filter:
            pipeline += [
                {"$addFields": {"_amt": {"$toDouble": "$gemini.json.total_amount"}}},
                {"$match": {"_amt": amount_filter}},
            ]
        pipeline += [
            {"$sort": {"_id": -1}},
            {"$limit": limit},
            {
                "$project": {
                    "_id": 0,
                    "invoice_number": {"$ifNull": ["$gemini.json.invoice_number", "$gemini.json.invoice"]},
                    "invoice_date":   "$gemini.json.invoice_date",
                    "seller":         "$gemini.json.seller",
                    "buyer":          "$gemini.json.buyer",
                    "total_amount":   "$gemini.json.total_amount",
                    "status":         "$gemini.json.additional_fields.status",
                    "hitl":           "$gemini.json.additional_fields.HITL",
                    "bill_month":     "$gemini.json.additional_fields.bill_for_month",
                }
            },
        ]
        docs = list(col.aggregate(pipeline))

        # Count via aggregation (consistent with $or / complex match conditions)
        count_pipeline = [{"$match": match}, {"$count": "total"}]
        if amount_filter:
            count_pipeline = [
                {"$match": match},
                {"$addFields": {"_amt": {"$toDouble": "$gemini.json.total_amount"}}},
                {"$match": {"_amt": amount_filter}},
                {"$count": "total"},
            ]
        count_result = list(col.aggregate(count_pipeline))
        total_matched = count_result[0]["total"] if count_result else 0

        return {
            "type": "list",
            "data": docs,
            "showing": len(docs),
            "total_matched": total_matched,
            "filters_applied": filters,
        }


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 3 — RESPONSE REFINER
# ─────────────────────────────────────────────────────────────────────────────

REFINE_SYSTEM_PROMPT = """
You are a professional invoice assistant. Follow these formatting rules EXACTLY for every response.

=== INVOICE LIST / SEARCH RESULTS (type: list, date_range_list) ===
Structure your response in this exact order:

1. HEADLINE (bold): One sentence directly answering the user's question.
   Example: **The most recent invoice is #SSS/25-26/00832, dated 31 Jan 2026, for ₹14,71,997.00.**

2. SUMMARY LINE: "Found X invoice(s) | Total: ₹Y" — on its own line, nothing else.

3. TABLE: Present ALL invoices as a markdown table with these exact columns:
   | Invoice Number | Date | Amount | Status |
   |---|---|---:|---|
   - Date: format as DD Mon YYYY (e.g. 31 Jan 2026). If missing, show ⚠️ Missing date
   - Amount: ₹ with comma separators and 2 decimal places, right-aligned (e.g. ₹14,71,997.00)
   - Status: 0 → Auto-processed | 1 → HITL Pending | 2 → HITL Reviewed. Never show raw numbers.
   - Sort rows by date, most recent first. Never repeat the same invoice twice.

4. OPTIONAL TIP: Only if genuinely useful, ONE follow-up: 💡 Try asking: "..."
   Skip this entirely if the answer is complete and obvious.

=== OTHER RESULT TYPES ===
- count: State the number directly. Example: "There are 12 invoices matching your query."
- sum: State total with ₹ symbol. Example: "Total amount across 7 invoices: ₹49,43,790.40"
- average: State average with ₹ symbol and invoice count.
- breakdown: Show the status breakdown exactly as:
    Total Processed: X
      System Processed : X
      HITL Pending     : X
      HITL Reviewed    : X
- duplicate: State total duplicate groups and total docs. Then show:
    | Invoice Number | Appears | Dates |
    |---|---:|---|
    One row per duplicate group.
- If no data found: "No invoices found for that query." then ONE suggestion for a broader search.

=== UNIVERSAL RULES ===
- Currency: always ₹ symbol, comma separators, 2 decimal places (e.g. ₹1,26,024.00)
- NEVER use raw field names (gemini.json, _id, etc.)
- NEVER mix summary text and itemized data in the same paragraph
- NEVER show more than 1 follow-up suggestion
- NEVER produce pipe-delimited lines outside a proper markdown table
"""


def refine_response(
    user_query: str,
    raw_result: dict,
    *,
    session_id: str | None = None,
) -> str:
    """Layer 3: Gemini formats raw MongoDB result → clean human-readable answer."""
    metrics_context: dict[str, Any] = {"source": "chat_refine"}
    if session_id:
        metrics_context["session_id"] = session_id
    # De-duplicate list results before sending to Gemini
    if raw_result.get("type") == "list" and isinstance(raw_result.get("data"), list):
        seen = set()
        deduped = []
        for item in raw_result["data"]:
            key = item.get("invoice_number") or str(item)
            if key not in seen:
                seen.add(key)
                deduped.append(item)
        raw_result = {**raw_result, "data": deduped}

    prompt = f"""{REFINE_SYSTEM_PROMPT}

User question: "{user_query}"

Data from database:
{json.dumps(raw_result, indent=2, default=str)}

Reply now (follow the formatting rules strictly):"""

    response = _get_gemini().generate_content(prompt, metrics_context=metrics_context)
    return response.text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# OFFLINE KEYWORD INTENT (no Gemini needed)
# ─────────────────────────────────────────────────────────────────────────────

def _keyword_intent(query: str) -> dict:
    """
    Simple regex-based intent builder used when Gemini is unavailable.
    Handles the most common query patterns without any LLM.
    """
    q = query.lower()
    filters: dict = {}

    # Status / HITL
    if any(k in q for k in ("hitl", "pending review", "pending", "human review", "waiting approval")):
        filters["hitl"] = True
        filters["status"] = 1
    elif any(k in q for k in ("auto-processed", "auto processed", "system processed", "no hitl")):
        filters["status"] = 0
    elif any(k in q for k in ("hitl reviewed", "human reviewed", "human approved", "hitl done")):
        filters["status"] = 2

    # Seller keyword
    for kw in ("surabhi", "maruti", "rebel", "kre38", "tidy", "bigtre", "makemytrip", "johnson"):
        if kw in q:
            filters["seller"] = kw
            break

    # Intent type
    if any(k in q for k in ("how many", "count", "number of")):
        intent_type = "count"
    elif any(k in q for k in ("total amount", "sum", "how much", "amount")):
        intent_type = "sum"
    elif any(k in q for k in ("average", "avg")):
        intent_type = "average"
    elif any(k in q for k in ("duplicate", "duplicated", "repeated", "same invoice")):
        intent_type = "duplicate"
    elif any(k in q for k in ("trend", "month wise", "breakdown")):
        intent_type = "trend"
    else:
        intent_type = "list"

    return {"intent_type": intent_type, "filters": filters, "limit": 10}


def _format_raw_result(raw_result: dict) -> str:
    """
    Plain Python formatter used when Gemini Layer 3 is unavailable.
    Always returns clean readable text — no pipe tables.
    """
    rtype = raw_result.get("type")

    if rtype == "breakdown":
        return _format_breakdown(raw_result)

    if rtype == "duplicate":
        groups = raw_result.get("duplicate_groups", 0)
        total  = raw_result.get("total_duplicate_docs", 0)
        data   = raw_result.get("data", [])
        if groups == 0:
            return "No duplicate invoice numbers found in the database."
        out = [f"Found {groups} duplicate invoice number(s) across {total} document(s):"]
        for d in data[:15]:
            inv_num = d.get("_id") or "Unknown"
            count   = d.get("count", 0)
            out.append(f"  \u2022 {inv_num} \u2014 appears {count} times")
        return "\n".join(out)


    if rtype == "count":
        val = raw_result.get("value", 0)
        return f"There are {val} invoice(s) matching your query."

    if rtype == "sum":
        total = raw_result.get("total_amount", 0)
        count = raw_result.get("invoice_count", 0)
        try:
            amt = float(total)
            if amt >= 1_00_00_000:
                fmt = f"₹{amt/1_00_00_000:.2f} Cr"
            elif amt >= 1_00_000:
                fmt = f"₹{amt/1_00_000:.2f} Lakh"
            else:
                fmt = f"₹{amt:,.0f}"
        except (TypeError, ValueError):
            fmt = str(total)
        return f"Total amount across {count} invoice(s): {fmt}"

    if rtype == "average":
        avg = raw_result.get("average_amount", 0) or raw_result.get("avg_amount", 0)
        try:
            amt = float(avg)
            fmt = f"₹{amt:,.0f}"
        except (TypeError, ValueError):
            fmt = str(avg)
        count = raw_result.get("invoice_count", 0)
        return f"Average invoice value across {count} invoice(s): {fmt}"

    if rtype == "trend":
        rows = raw_result.get("data", [])
        if not rows:
            return "No data found for trend analysis."
        lines = ["Month-wise breakdown:"]
        for row in rows:
            month = row.get("_id") or row.get("month") or "Unknown"
            amt   = row.get("total_amount", 0)
            cnt   = row.get("invoice_count", 0)
            try:
                a = float(amt)
                af = f"₹{a:,.0f}"
            except Exception:
                af = str(amt)
            lines.append(f"  {month}: {cnt} invoice(s), {af}")
        return "\n".join(lines)

    if rtype in ("list", "detail"):
        data = raw_result.get("data") or []
        if not data:
            return "No invoices found matching your query."
        if isinstance(data, dict):
            data = [data]
        total_matched = raw_result.get("total_matched", len(data))
        showing       = raw_result.get("showing", len(data))
        lines = [f"Showing {showing} of {total_matched} invoice(s):"]
        for i, inv in enumerate(data, 1):
            num    = inv.get("invoice_number") or "N/A"
            seller = inv.get("seller") or "Unknown"
            amt    = inv.get("total_amount") or inv.get("amount") or "N/A"
            date   = inv.get("invoice_date") or inv.get("date") or "N/A"
            status_map = {0: "Auto-processed", 1: "HITL Pending", 2: "HITL Reviewed"}
            status = status_map.get(inv.get("status"), "Unknown")
            lines.append(f"  {i}. {num} | {seller} | {date} | ₹{amt} | {status}")
        return "\n".join(lines)

    return json.dumps(raw_result, indent=2, default=str)


# ─────────────────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────────────────

def _fmt_amount(val) -> str:
    """Format a value as ₹ with Indian comma separators and 2 decimal places."""
    try:
        f = float(str(val).replace(",", "").strip())
        # Indian comma grouping: last 3 digits, then groups of 2
        s = f"{f:.2f}"
        integer, decimal = s.split(".")
        if len(integer) > 3:
            # Apply Indian grouping
            rev = integer[::-1]
            parts = [rev[:3]] + [rev[i:i+2] for i in range(3, len(rev), 2)]
            integer = ",".join(parts)[::-1]
        return f"\u20b9{integer}.{decimal}"
    except Exception:
        return str(val) if val is not None else "N/A"


def _fmt_date(raw: str | None) -> str:
    """Parse and format invoice date as DD Mon YYYY. Returns warning span if missing."""
    if not raw or str(raw).strip().lower() in ("none", "null", "n/a", ""):
        return '<span class="chat-invoice-warning">\u26a0\ufe0f Missing date</span>'
    parsed = _parse_invoice_date(str(raw))
    if parsed is None:
        return '<span class="chat-invoice-warning">\u26a0\ufe0f Missing date</span>'
    return parsed.strftime("%d %b %Y")


def _status_badge(status_val) -> str:
    """Return a coloured status badge HTML span."""
    try:
        s = int(status_val)
    except (TypeError, ValueError):
        return '<span class="badge badge--unknown">Unknown</span>'
    if s == 0:
        return '<span class="badge badge--auto">Auto-processed</span>'
    if s == 1:
        return '<span class="badge badge--hitl-p">HITL Pending</span>'
    if s == 2:
        return '<span class="badge badge--hitl-r">HITL Reviewed</span>'
    return '<span class="badge badge--unknown">Unknown</span>'


def _build_html_response(raw_result: dict, user_query: str) -> str | None:
    """
    Build a rich HTML response for list/date_range_list/duplicate results.
    Returns None for result types that should still go through Gemini.
    """
    rtype = raw_result.get("type")

    # ── DUPLICATE ──────────────────────────────────────────────────────────────
    if rtype == "duplicate":
        groups = raw_result.get("duplicate_groups", 0)
        total  = raw_result.get("total_duplicate_docs", 0)
        data   = raw_result.get("data") or []
        if groups == 0:
            return (
                '<div class="chat-invoice-result">'
                '<p class="chat-invoice-headline">'
                "\u2705 No duplicate invoice numbers found in the database."
                "</p></div>"
            )
        rows_html = ""
        for d in data:
            inv = d.get("_id") or "Unknown"
            cnt = d.get("count", 0)
            dates = ", ".join(str(x) for x in (d.get("invoice_dates") or []) if x) or "N/A"
            rows_html += (
                f"<tr><td>{inv}</td>"
                f'<td class="col-amount">{cnt}</td>'
                f"<td>{dates}</td></tr>"
            )
        return (
            '<div class="chat-invoice-result">'
            f'<p class="chat-invoice-headline">'
            f"Found <strong>{groups}</strong> duplicate invoice number(s) across "
            f"<strong>{total}</strong> document(s)."
            "</p>"
            '<table class="chat-invoice-table">'
            "<thead><tr><th>Invoice Number</th><th>Count</th><th>Dates</th></tr></thead>"
            f"<tbody>{rows_html}</tbody>"
            "</table></div>"
        )

    # ── LIST / DATE_RANGE_LIST ─────────────────────────────────────────────────
    if rtype not in ("list", "date_range_list"):
        return None  # let Gemini handle counts, sums, trends etc.

    data = raw_result.get("data") or []
    if not data:
        return (
            '<div class="chat-invoice-result">'
            '<p class="chat-invoice-plain">No invoices found for that query.</p>'
            "</div>"
        )

    # Sort by parsed date descending
    def _sort_key(inv):
        d = _parse_invoice_date(inv.get("invoice_date") or inv.get("date"))
        from datetime import date as _date
        return d or _date.min
    data = sorted(data, key=_sort_key, reverse=True)

    total_matched = raw_result.get("total_matched", len(data))
    showing       = raw_result.get("showing", len(data))

    # Compute total amount
    from backend.hitl_status import _to_float as _tf
    total_amt = raw_result.get("total_amount") or sum(
        _tf(inv.get("total_amount")) or 0 for inv in data
    )

    # Headline: answer the user's specific question
    if data:
        newest = data[0]
        inv_num = newest.get("invoice_number") or "N/A"
        inv_date = _fmt_date(newest.get("invoice_date") or newest.get("date"))
        inv_amt  = _fmt_amount(newest.get("total_amount"))
        q_lower = user_query.lower()
        if any(k in q_lower for k in ("recent", "latest", "newest", "last")):
            headline = (
                f"The most recent invoice is <strong>#{inv_num}</strong>, "
                f"dated {inv_date}, for <strong>{inv_amt}</strong>."
            )
        else:
            headline = (
                f"Found <strong>{total_matched}</strong> invoice(s) matching your query."
            )
    else:
        headline = "No invoices found."

    summary = (
        f"Showing {showing} of {total_matched} invoice(s)"
        + (f" &nbsp;|&nbsp; Total: <strong>{_fmt_amount(total_amt)}</strong>" if total_amt else "")
    )

    rows_html = ""
    for inv in data:
        num    = inv.get("invoice_number") or inv.get("invoice") or "N/A"
        date   = _fmt_date(inv.get("invoice_date") or inv.get("date"))
        amt    = _fmt_amount(inv.get("total_amount") or inv.get("amount"))
        badge  = _status_badge(inv.get("status"))
        rows_html += (
            f"<tr>"
            f"<td>{num}</td>"
            f"<td>{date}</td>"
            f'<td class="col-amount">{amt}</td>'
            f"<td>{badge}</td>"
            f"</tr>"
        )

    return (
        '<div class="chat-invoice-result">'
        f'<p class="chat-invoice-headline">{headline}</p>'
        f'<p class="chat-invoice-summary">{summary}</p>'
        '<table class="chat-invoice-table">'
        "<thead><tr>"
        "<th>Invoice Number</th><th>Date</th><th>Amount</th><th>Status</th>"
        "</tr></thead>"
        f"<tbody>{rows_html}</tbody>"
        "</table>"
        "</div>"
    )


def run_pipeline(
    user_query: str,
    chat_history: list[dict] | None = None,
    *,
    session_id: str | None = None,
) -> str:
    """
    Run all 3 layers and return the final answer string.
    Any Gemini error propagates up — chat_engine.py shows the error message to the user.
    """
    if chat_history is None:
        chat_history = []

    # Relevance gate — reject off-topic queries before hitting MongoDB
    if not _is_invoice_related(user_query, session_id=session_id):
        logger.info("[Pipeline] Off-topic query rejected: %r", user_query)
        return "That question isn't related to the invoice management application. Please ask about invoices, billing, vendors, payment status, or related topics."

    # Layer 1 — intent extraction (Gemini)
    intent = extract_intent(user_query, chat_history, session_id=session_id)
    logger.info("[Pipeline] Intent: type=%s filters=%s", intent.get("intent_type"), intent.get("filters"))

    # Layer 2 — MongoDB query (pure Python, always works)
    raw_result = execute_query(intent)
    logger.info("[Pipeline] Query result type: %s", raw_result.get("type"))

    # Breakdown formatted by Python
    if raw_result.get("type") == "breakdown":
        return _format_breakdown(raw_result)

    # List / duplicate — build rich HTML directly in Python (no Gemini needed)
    html = _build_html_response(raw_result, user_query)
    if html is not None:
        return html

    # Layer 3 — response refinement (Gemini) for counts, sums, trends, etc.
    return refine_response(user_query, raw_result, session_id=session_id)


def _format_breakdown(result: dict) -> str:
    total    = result.get("total", 0)
    auto     = result.get("system_processed", 0)
    pending  = result.get("hitl_pending", 0)
    reviewed = result.get("hitl_reviewed", 0)

    lines = [
        f"Total Processed: {total}",
        "",
        f"  System Processed  : {auto}",
        f"  HITL Pending      : {pending}",
        f"  HITL Reviewed     : {reviewed}",
        "",
        '\xf0\x9f\x92\xa1 Try asking: "Show HITL pending invoices" or "List auto-processed invoices"',
    ]
    return "\n".join(lines)
