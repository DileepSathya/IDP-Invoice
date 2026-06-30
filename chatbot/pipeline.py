"""
Invoice Chatbot — 3-Layer Pipeline
───────────────────────────────────
Layer 1  →  Intent Engine     (Gemini understands messy user query)
Layer 2  →  Query Builder     (Python builds safe MongoDB query from intent)
Layer 3  →  Response Refiner  (Gemini formats raw data into clean answer)
"""
import logging
import json
import re
from datetime import datetime, timezone, timedelta
import google.generativeai as genai
from pymongo import MongoClient
from config import (
    MONGO_URI, MONGO_DB, MONGO_COLLECTION,
    GEMINI_API_KEY, GEMINI_MODEL, MAX_LIST_RESULTS,
)
logger = logging.getLogger(__name__)
# ─────────────────────────────────────────────────────────────────────────────
# SETUP
# ─────────────────────────────────────────────────────────────────────────────

genai.configure(api_key=GEMINI_API_KEY)
_gemini = genai.GenerativeModel(GEMINI_MODEL)
_mongo  = MongoClient(MONGO_URI)
_col    = _mongo[MONGO_DB][MONGO_COLLECTION]


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 1 — INTENT ENGINE
# ─────────────────────────────────────────────────────────────────────────────

def _build_intent_prompt() -> str:
    """Build INTENT_SYSTEM_PROMPT with today's date injected so Gemini can resolve relative dates."""
    today = datetime.now(timezone.utc)
    today_str = today.strftime("%Y-%m-%d")
    yesterday_str = (today - timedelta(days=1)).strftime("%Y-%m-%d")
    week_start_str = (today - timedelta(days=today.weekday())).strftime("%Y-%m-%d")
    month_start_str = today.replace(day=1).strftime("%Y-%m-%d")

    return f"""
You are an invoice database query interpreter.
Your job is to understand a user's natural language query about invoices
and return a STRICT JSON object — nothing else, no markdown, no explanation.

=== Today's Date (UTC) ===
Today      : {today_str}
Yesterday  : {yesterday_str}
This week  : {week_start_str} to {today_str}
This month : {month_start_str} to {today_str}

=== MongoDB Document Schema ===
- gemini.json.invoice_number        : string  e.g. "SSS/25-26/00832"
- gemini.json.invoice_date          : string  e.g. "31/01/2026"  (date written on the invoice)
- gemini.json.seller                : string  e.g. "SURABHI INTEGRATED SERVICES"
- gemini.json.buyer                 : string  e.g. "MY HOME JEWEL FLAT OWNERS..."
- gemini.json.total_amount          : string  (numeric value stored as string)
- gemini.json.line_items[].service  : string  e.g. "SECURITY GUARD", "SECURITY SUPERVISOR"
- gemini.json.line_items[].amount   : string  (numeric value stored as string)
- gemini.json.additional_fields.status       : int  0=processing, 1=completed, 2=failed
- gemini.json.additional_fields.HITL         : bool true=needs human review, false=auto-processed
- gemini.json.additional_fields.bill_for_month : string  e.g. "January-2026"
- gemini.json.additional_fields.seller_gstin : string
- gemini.json.additional_fields.buyer_gstin  : string
- file_status                       : string  "healthy file" or "corrupted"
- created_at                        : ISODate  (when invoice was uploaded/created in system)
- processed_at                      : ISODate  (when invoice was processed by the pipeline)

=== Output Format ===
Return ONLY this JSON, no extra text:
{{
  "intent_type": "<count|sum|list|detail|average|trend|irrelevant>",
  "filters": {{
    "seller": "<substring or null>",
    "buyer": "<substring or null>",
    "status": <0|1|2|null>,
    "hitl": <true|false|null>,
    "bill_month": "<e.g. January-2026 or null>",
    "invoice_number": "<exact or null>",
    "amount_gt": <number or null>,
    "amount_lt": <number or null>,
    "service_type": "<e.g. SECURITY GUARD or null>",
    "file_status": "<healthy file|corrupted|null>",
    "processed_date_from": "<ISO date YYYY-MM-DD or null>",
    "processed_date_to": "<ISO date YYYY-MM-DD or null>"
  }},
  "limit": <number, default 10>,
  "clarification_needed": "<question to ask user, or null>"
}}

=== Intent Type Rules ===
- count   : user wants a number/count   e.g. "how many invoices", "count of"
- sum     : user wants a total amount   e.g. "total amount", "sum of bills"
- list    : user wants a list           e.g. "show me", "list all", "which invoices"
- detail  : user wants one specific invoice  e.g. "show invoice SSS/25-26/00832"
- average : user wants an average       e.g. "average invoice value"
- trend   : user wants monthly/time breakdown  e.g. "month wise", "trend"

=== Status Mapping ===
processing / pending / in-progress  → 0
completed / done / finished         → 1
failed / error / rejected           → 2

=== Date Filter Rules ===
IMPORTANT: "today", "yesterday", "this week", "this month" refer to when the invoice
was PROCESSED (processed_at field), NOT the date written on the invoice.
- "today" / "processed today"         → processed_date_from={today_str}, processed_date_to={today_str}
- "yesterday"                          → processed_date_from={yesterday_str}, processed_date_to={yesterday_str}
- "this week"                          → processed_date_from={week_start_str}, processed_date_to={today_str}
- "this month"                         → processed_date_from={month_start_str}, processed_date_to={today_str}
- "on 2026-05-01"                      → processed_date_from=2026-05-01, processed_date_to=2026-05-01
- No date mentioned                    → both null (do NOT assume a date)

=== Examples ===
"total amount" → intent_type=sum, all filters null
"how many invoices processed today" → intent_type=count, processed_date_from={today_str}, processed_date_to={today_str}
"how many invoices processing" → intent_type=count, status=0
"show surabhi january invoices" → intent_type=list, seller="surabhi", bill_month="January"
"invoices needing human review" → intent_type=list, hitl=true
"average bill value for security guard" → intent_type=average, service_type="SECURITY GUARD"
"corrupted files" → intent_type=list, file_status="corrupted"
"invoices processed this week" → intent_type=list, processed_date_from={week_start_str}, processed_date_to={today_str}

=== Irrelevant Query Rule ===
If the user's query has NOTHING to do with invoices, billing, payments, amounts, vendors, buyers, or
this invoice management system — for example general knowledge questions like "who is the prime minister
of India?", "what is 2+2?", "tell me a joke" — return EXACTLY this JSON and nothing else:
{{"intent_type": "irrelevant", "filters": {{}}, "limit": 0, "clarification_needed": null}}
"""


_RELEVANCE_PROMPT = """You are a classifier for an invoice management system.
Determine if the user's question is related to THIS application's domain.

Relevant topics: invoices, billing, payments, vendors, buyers, amounts, invoice status,
processing status, HITL review, corrupted files, sellers, GST, invoice numbers, bill months.

NOT relevant: general knowledge, math, geography, history, politics, science, sports,
entertainment, recipes, weather, coding help unrelated to this app, or anything not
about invoice/billing data.

Reply with ONLY one word: YES or NO

User question: {query}"""


def _is_invoice_related(user_query: str) -> bool:
    """Quick binary relevance check before running the full intent pipeline."""
    try:
        response = _gemini.generate_content(
            _RELEVANCE_PROMPT.format(query=user_query)
        )
        answer = response.text.strip().upper()
        return answer.startswith("Y")
    except Exception:
        return True  # on error, allow through


def extract_intent(user_query: str, chat_history: list[dict]) -> dict:
    logger.info("Entering layer -1")
    """
    Layer 1: Use Gemini to convert messy user query → structured intent JSON.
    chat_history format: [{"role": "user"|"assistant", "content": "..."}]
    """
    # Build context from recent history (last 4 turns)
    history_text = ""
    for turn in chat_history[-4:]:
        role = "User" if turn["role"] == "user" else "Assistant"
        history_text += f"{role}: {turn['content']}\n"

    prompt = f"{_build_intent_prompt()}\n\n"
    if history_text:
        prompt += f"=== Recent Conversation ===\n{history_text}\n"
    prompt += f"=== Current Query ===\n{user_query}"

    response = _gemini.generate_content(prompt)
    raw = response.text.strip()

    # Strip markdown code fences if Gemini wraps in ```json ... ```
    raw = re.sub(r"^```(?:json)?\s*", "", raw)
    raw = re.sub(r"\s*```$", "", raw)

    try:
         
        return json.loads(raw)
    except json.JSONDecodeError:
        # Fallback: treat as a generic list query
        return {
            "intent_type": "irrelevant",
            "filters": {},
            "limit": MAX_LIST_RESULTS,
            "clarification_needed": None,
        }


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 2 — QUERY BUILDER + EXECUTOR
# ─────────────────────────────────────────────────────────────────────────────

def _build_match(filters: dict) -> dict:
    """Convert intent filters dict → MongoDB $match conditions."""
    match = {}

    if filters.get("seller"):
        match["gemini.json.seller"] = {"$regex": filters["seller"], "$options": "i"}

    if filters.get("buyer"):
        match["gemini.json.buyer"] = {"$regex": filters["buyer"], "$options": "i"}

    if filters.get("status") is not None:
        match["gemini.json.additional_fields.status"] = filters["status"]

    if filters.get("hitl") is not None:
        match["gemini.json.additional_fields.HITL"] = filters["hitl"]

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

    # Processed date range filter — uses processed_at (falls back to created_at for old docs)
    date_from = filters.get("processed_date_from")
    date_to   = filters.get("processed_date_to")
    if date_from or date_to:
        date_match: dict = {}
        if date_from:
            date_match["$gte"] = datetime.strptime(date_from, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        if date_to:
            # include the full end day (up to 23:59:59)
            date_match["$lte"] = datetime.strptime(date_to, "%Y-%m-%d").replace(
                hour=23, minute=59, second=59, tzinfo=timezone.utc
            )
        # processed_at exists on newly stored docs; fall back to created_at for older docs
        match["$or"] = [
            {"processed_at": date_match},
            {"processed_at": {"$exists": False}, "created_at": date_match},
        ]

    # Amount range filters — need $toDouble since total_amount is stored as string
    amount_filter = {}
    if filters.get("amount_gt") is not None:
        amount_filter["$gt"] = filters["amount_gt"]
    if filters.get("amount_lt") is not None:
        amount_filter["$lt"] = filters["amount_lt"]
    if amount_filter:
        # We'll apply this in the aggregation pipeline after converting to number
        match["_amount_filter"] = amount_filter  # handled in execute_query

    return match


def execute_query(intent: dict) -> dict:
    """
    Layer 2: Build + run MongoDB query from intent. Returns structured result.
    """
    intent_type = intent.get("intent_type", "list")
    filters     = intent.get("filters", {}) or {}
    limit       = intent.get("limit", MAX_LIST_RESULTS) or MAX_LIST_RESULTS

    match = _build_match(filters)
    amount_filter = match.pop("_amount_filter", None)

    # ── COUNT ─────────────────────────────────────────────────────────────────
    if intent_type == "count":
        pipeline = [{"$match": match}]
        if amount_filter:
            pipeline += [
                {"$addFields": {"_amt": {"$toDouble": "$gemini.json.total_amount"}}},
                {"$match": {"_amt": amount_filter}},
            ]
        pipeline.append({"$count": "total"})
        result = list(_col.aggregate(pipeline))
        count  = result[0]["total"] if result else 0
        return {"type": "count", "value": count, "filters_applied": filters}

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
        result = list(_col.aggregate(pipeline))
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
        result = list(_col.aggregate(pipeline))
        if result:
            return {
                "type": "average",
                "avg_amount": result[0]["avg_amount"],
                "invoice_count": result[0]["invoice_count"],
                "filters_applied": filters,
            }
        return {"type": "average", "avg_amount": 0, "invoice_count": 0, "filters_applied": filters}

    # ── TREND (month-wise breakdown) ──────────────────────────────────────────
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
        result = list(_col.aggregate(pipeline))
        return {"type": "trend", "data": result, "filters_applied": filters}

    # ── DETAIL (single invoice) ───────────────────────────────────────────────
    elif intent_type == "detail":
        doc = _col.find_one(match, {"_id": 0, "gemini.json": 1, "file_path": 1})
        return {"type": "detail", "data": doc, "filters_applied": filters}

    # ── LIST (default) ────────────────────────────────────────────────────────
    else:
        pipeline = [{"$match": match}]
        if amount_filter:
            pipeline += [
                {"$addFields": {"_amt": {"$toDouble": "$gemini.json.total_amount"}}},
                {"$match": {"_amt": amount_filter}},
            ]
        pipeline += [
            {"$limit": limit},
            {
                "$project": {
                    "_id": 0,
                    "invoice_number": "$gemini.json.invoice_number",
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
        docs = list(_col.aggregate(pipeline))
        total_count = _col.count_documents(match)
        return {
            "type": "list",
            "data": docs,
            "showing": len(docs),
            "total_matched": total_count,
            "filters_applied": filters,
        }


# ─────────────────────────────────────────────────────────────────────────────
# LAYER 3 — RESPONSE REFINER
# ─────────────────────────────────────────────────────────────────────────────

REFINE_SYSTEM_PROMPT = """
You are a helpful invoice assistant. The user asked a question about their invoice database.
You have already retrieved the relevant data from MongoDB.

Your job:
1. Answer the user's question clearly and concisely using the retrieved data.
2. Use Indian number formatting (lakhs/crores) for amounts above 1,00,000.
3. Format amounts with ₹ symbol.
4. For lists, present as a clean numbered list with key details.
5. For counts/sums, lead with the key number and add brief context.
6. If data is empty, say "No invoices found" and suggest the user try broader terms.
7. Keep it conversational — you're a helpful assistant, not a database dump.
8. Never expose raw MongoDB field names or internal structure to the user.

Status codes: 0 = Processing, 1 = Completed, 2 = Failed
"""


def refine_response(user_query: str, raw_result: dict, chat_history: list[dict]) -> str:
    """
    Layer 3: Use Gemini to convert raw MongoDB result → clean human-readable answer.
    """
    prompt = f"""{REFINE_SYSTEM_PROMPT}

User question: {user_query}

Retrieved data:
{json.dumps(raw_result, indent=2, default=str)}

Write a clear, helpful answer:"""

    response = _gemini.generate_content(prompt)
    return response.text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CHAT FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def chat(user_query: str, chat_history: list[dict] | None = None) -> dict:
    """
    Orchestrate all 3 layers. Returns:
    {
        "answer":  str,          # final refined answer
        "intent":  dict,         # extracted intent (for debugging)
        "raw":     dict,         # raw MongoDB result (for debugging)
        "error":   str | None,   # error message if something failed
    }
    """
    if chat_history is None:
        chat_history = []

    _IRRELEVANT = {
        "answer":  "That question isn't related to the invoice management application. Please ask about invoices, billing, vendors, payment status, or related topics.",
        "intent":  {},
        "raw":     {},
        "error":   None,
    }

    try:
        # Pre-check: reject off-topic queries before running the full pipeline
        if not _is_invoice_related(user_query):
            return _IRRELEVANT

        # Layer 1 — understand the query
        intent = extract_intent(user_query, chat_history)

        # Fallback: catch any irrelevant intent that slipped through
        if "irrelevant" in (intent.get("intent_type") or ""):
            return _IRRELEVANT

        # If Gemini needs clarification, ask the user
        if intent.get("clarification_needed"):
            return {
                "answer":  intent["clarification_needed"],
                "intent":  intent,
                "raw":     {},
                "error":   None,
            }

        # Layer 2 — fetch data from MongoDB
        raw_result = execute_query(intent)

        # Layer 3 — format the response
        answer = refine_response(user_query, raw_result, chat_history)

        return {"answer": answer, "intent": intent, "raw": raw_result, "error": None}

    except Exception as e:
        return {
            "answer": "Sorry, I ran into an error processing your query. Please try again.",
            "intent": {},
            "raw":    {},
            "error":  str(e),
        }
"""an number formatting (lakhs/crores) for amounts above 1,00,000.
3. Format amounts with ₹ symbol.
4. For lists, present as a clean numbered list with key details.
5. For counts/sums, lead with the key number and add brief context.
6. If data is empty, say "No invoices found" and suggest the user try broader terms.
7. Keep it conversational — you're a helpful assistant, not a database dump.
8. Never expose raw MongoDB field names or internal structure to the user.

Status codes: 0 = Processing, 1 = Completed, 2 = Failed
"""


def refine_response(user_query: str, raw_result: dict, chat_history: list[dict]) -> str:
    """
    Layer 3: Use Gemini to convert raw MongoDB result → clean human-readable answer.
    """
    prompt = f"""{REFINE_SYSTEM_PROMPT}

User question: {user_query}

Retrieved data:
{json.dumps(raw_result, indent=2, default=str)}

Write a clear, helpful answer:"""

    response = _gemini.generate_content(prompt)
    return response.text.strip()


# ─────────────────────────────────────────────────────────────────────────────
# MAIN CHAT FUNCTION
# ─────────────────────────────────────────────────────────────────────────────

def chat(user_query: str, chat_history: list[dict] | None = None) -> dict:
    """
    Orchestrate all 3 layers. Returns:
    {
        "answer":  str,          # final refined answer
        "intent":  dict,         # extracted intent (for debugging)
        "raw":     dict,         # raw MongoDB result (for debugging)
        "error":   str | None,   # error message if something failed
    }
    """
    if chat_history is None:
        chat_history = []

    _IRRELEVANT = {
        "answer":  "That question isn't related to the invoice management application. Please ask about invoices, billing, vendors, payment status, or related topics.",
        "intent":  {},
        "raw":     {},
        "error":   None,
    }

    try:
        # Pre-check: reject off-topic queries before running the full pipeline
        if not _is_invoice_related(user_query):
            return _IRRELEVANT
       
        # Layer 1 — understand the query
        intent = extract_intent(user_query, chat_history)
       

        # Fallback: catch any irrelevant intent that slipped through
        if "irrelevant" in (intent.get("intent_type") or ""):
            return _IRRELEVANT

        # If Gemini needs clarification, ask the user
        if intent.get("clarification_needed"):
            return {
                "answer":  intent["clarification_needed"],
                "intent":  intent,
                "raw":     {},
                "error":   None,
            }
        logger.info("Entering layer -2")
        # Layer 2 — fetch data from MongoDB
        raw_result = execute_query(intent)
        logger.info("layer -2 completed")
        # Layer 3 — format the response
        answer = refine_response(user_query, raw_result, chat_history)

        return {"answer": answer, "intent": intent, "raw": raw_result, "error": None}

    except Exception as e:
        return {
            "answer": "Sorry, I ran into an error processing your query. Please try again.",
            "intent": {},
            "raw":    {},
            "error":  str(e),
        }
