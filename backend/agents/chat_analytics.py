"""Structured invoice analytics over the full MongoDB collection."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import Any, Iterator

from backend.agents.database import get_invoices_collection
from backend.agents.rag_chatbot import _extract_gemini_json, _first

_AMOUNT_RE = re.compile(r"[^\d.\-]")

_DAYS_RE = re.compile(
    r"(?:past|last|previous|in the past|over the past|from the past)\s+(\d+)\s+days?",
    re.IGNORECASE,
)
_WEEK_RE = re.compile(
    r"(?:past|last|previous)\s+(?:week|7\s+days?)",
    re.IGNORECASE,
)
_MONTH_RE = re.compile(
    r"(?:past|last|previous)\s+(?:month|30\s+days?)",
    re.IGNORECASE,
)
_YESTERDAY_RE = re.compile(r"\byesterday\b", re.IGNORECASE)
_TODAY_RE = re.compile(
    r"\b(?:today|processed today|this day)\b",
    re.IGNORECASE,
)

_ANALYTICS_RE = re.compile(
    r"(how many|count|number of|total|sum|amount|invoices?|processed)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class DateRange:
    since: datetime
    label: str


@dataclass(frozen=True)
class InvoiceRollup:
    count: int
    amount_sum: float
    amount_count: int
    invoices: list[dict[str, str]]


def _utc_midnight(d: date) -> datetime:
    return datetime.combine(d, time.min)


def _parse_amount(raw: Any) -> float | None:
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    cleaned = _AMOUNT_RE.sub("", text)
    if not cleaned or cleaned in {".", "-", "-."}:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _invoice_amount(gemini_json: dict[str, Any]) -> float | None:
    return _parse_amount(
        _first(
            [
                gemini_json.get("total_amount"),
                gemini_json.get("grand_total"),
                gemini_json.get("amount"),
                gemini_json.get("summary_total_amount"),
            ]
        )
    )


def parse_date_range(question: str) -> DateRange | None:
    q = question.strip()
    if not q:
        return None

    match = _DAYS_RE.search(q)
    if match:
        days = max(1, int(match.group(1)))
        since = datetime.utcnow() - timedelta(days=days)
        label = f"the past {days} day{'s' if days != 1 else ''}"
        return DateRange(since=since, label=label)

    if _WEEK_RE.search(q):
        since = datetime.utcnow() - timedelta(days=7)
        return DateRange(since=since, label="the past 7 days")

    if _MONTH_RE.search(q):
        since = datetime.utcnow() - timedelta(days=30)
        return DateRange(since=since, label="the past 30 days")

    if _YESTERDAY_RE.search(q):
        since = _utc_midnight(date.today() - timedelta(days=1))
        return DateRange(since=since, label="yesterday onward (UTC)")

    if _TODAY_RE.search(q):
        since = _utc_midnight(date.today())
        return DateRange(since=since, label="today (UTC)")

    return None


def wants_analytics_summary(question: str) -> bool:
    q = question.strip()
    if not q:
        return False
    if not _ANALYTICS_RE.search(q):
        return False
    return parse_date_range(q) is not None or _TODAY_RE.search(q) is not None


def _iter_invoices_since(since: datetime) -> Iterator[dict[str, Any]]:
    coll = get_invoices_collection()
    query = {"created_at": {"$gte": since}}
    yield from coll.find(query, sort=[("created_at", -1)])


def _iter_all_invoices() -> Iterator[dict[str, Any]]:
    coll = get_invoices_collection()
    yield from coll.find({}, sort=[("created_at", -1)])


def rollup_invoices(docs: Iterator[dict[str, Any]], *, list_limit: int = 25) -> InvoiceRollup:
    amount_sum = 0.0
    amount_count = 0
    count = 0
    invoices: list[dict[str, str]] = []

    for doc in docs:
        count += 1
        gemini_json = _extract_gemini_json(doc)
        amount = _invoice_amount(gemini_json)
        if amount is not None:
            amount_sum += amount
            amount_count += 1

        if len(invoices) < list_limit:
            invoices.append(
                {
                    "invoice_number": _first(
                        [gemini_json.get("invoice_number"), gemini_json.get("invoice")],
                        "—",
                    ),
                    "seller": _first([gemini_json.get("seller")], "—"),
                    "date": _first([gemini_json.get("invoice_date"), gemini_json.get("date")], "—"),
                    "amount": _first(
                        [
                            gemini_json.get("total_amount"),
                            gemini_json.get("grand_total"),
                            gemini_json.get("amount"),
                        ],
                        "—",
                    ),
                    "processed_at": str(doc.get("created_at") or "—"),
                }
            )

    return InvoiceRollup(
        count=count,
        amount_sum=amount_sum,
        amount_count=amount_count,
        invoices=invoices,
    )


def format_rollup(rollup: InvoiceRollup, *, range_label: str, show_list: bool = True) -> str:
    lines = [
        f"Invoices processed in {range_label}: {rollup.count}",
        f"Sum of invoice totals (where parseable): {rollup.amount_sum:,.2f} "
        f"across {rollup.amount_count} invoice(s)",
    ]

    if not show_list or rollup.count == 0:
        if rollup.count == 0:
            lines.append("\nNo invoices were processed in this period.")
        return "\n".join(lines)

    lines.append("\nInvoices:")
    for row in rollup.invoices:
        lines.append(
            f"• {row['invoice_number']} | {row['seller']} | "
            f"Invoice date: {row['date']} | Total: {row['amount']}"
        )

    remaining = rollup.count - len(rollup.invoices)
    if remaining > 0:
        lines.append(f"\n… and {remaining} more invoice(s) in this period.")

    return "\n".join(lines)


def answer_date_range_analytics(question: str, *, list_limit: int = 500) -> str | None:
    date_range = parse_date_range(question)
    if date_range is None:
        return None
    if not wants_analytics_summary(question) and not _DAYS_RE.search(question):
        return None

    coll = get_invoices_collection()
    query = {"created_at": {"$gte": date_range.since}}
    total_in_period = int(coll.count_documents(query))
    effective_limit = total_in_period if total_in_period <= list_limit else list_limit

    docs = _iter_invoices_since(date_range.since)
    rollup = rollup_invoices(docs, list_limit=effective_limit)
    return format_rollup(rollup, range_label=date_range.label, show_list=True)


def answer_all_time_totals() -> str:
    rollup = rollup_invoices(_iter_all_invoices(), list_limit=0)
    return (
        f"Total invoices in database: {rollup.count}\n"
        f"Combined total amount (parseable): {rollup.amount_sum:,.2f} "
        f"across {rollup.amount_count} invoice(s)"
    )


_HITL_RE = re.compile(
    r"(hitl|human.{0,10}review|manual review|pending review|hitl.{0,10}flagged|flagged.{0,10}hitl)",
    re.IGNORECASE,
)
_TABLE_RE = re.compile(
    r"\b(table|tabular|as a table|in table form)\b|show\s+(?:me\s+)?(?:the\s+)?table",
    re.IGNORECASE,
)
_SHOW_LIST_RE = re.compile(
    r"\b(show|list|display|give me)\b",
    re.IGNORECASE,
)


def wants_table_display(question: str) -> bool:
    return bool(_TABLE_RE.search(question.strip()))


def wants_invoice_list(question: str) -> bool:
    q = question.strip()
    if not q:
        return False
    if wants_table_display(q):
        return True
    return bool(
        _SHOW_LIST_RE.search(q)
        and re.search(r"\b(invoice|invoices|them|those|details|results)\b", q, re.IGNORECASE)
    )


def wants_hitl_query(question: str) -> bool:
    return bool(_HITL_RE.search(question.strip()))


def hitl_pending_only(question: str) -> bool:
    q = question.strip()
    if re.search(r"\ball\s+(?:hitl\s+)?flagged\b", q, re.IGNORECASE):
        return False
    return True


def _hitl_flagged(additional_fields: dict[str, Any]) -> bool:
    return str(additional_fields.get("HITL", additional_fields.get("HIT", ""))).strip().lower() in {
        "true",
        "1",
        "yes",
    }


def _hitl_status_value(additional_fields: dict[str, Any]) -> int | None:
    try:
        raw = additional_fields.get("status")
        return int(raw) if raw is not None else None
    except Exception:
        return None


def _invoice_table_row(doc: dict[str, Any]) -> dict[str, str]:
    gemini_json = _extract_gemini_json(doc)
    return {
        "invoice_number": _first(
            [gemini_json.get("invoice_number"), gemini_json.get("invoice")],
            "—",
        ),
        "invoice_date": _first([gemini_json.get("invoice_date"), gemini_json.get("date")], "—"),
        "seller": _first([gemini_json.get("seller")], "—"),
        "buyer": _first([gemini_json.get("buyer"), gemini_json.get("customer")], "—"),
        "total_amount": _first(
            [
                gemini_json.get("total_amount"),
                gemini_json.get("grand_total"),
                gemini_json.get("amount"),
            ],
            "—",
        ),
    }


STANDARD_INVOICE_COLUMNS: list[tuple[str, str]] = [
    ("invoice_number", "Invoice #"),
    ("invoice_date", "Date"),
    ("seller", "Seller"),
    ("buyer", "Buyer"),
    ("total_amount", "Total"),
]


def _invoice_row(doc: dict[str, Any]) -> dict[str, str]:
    gemini_json = _extract_gemini_json(doc)
    additional = gemini_json.get("additional_fields") or {}
    if not isinstance(additional, dict):
        additional = {}
    row = _invoice_table_row(doc)
    row["hitl_status"] = "pending review" if _hitl_status_value(additional) == 1 else "—"
    return row


def iter_hitl_invoices(*, pending_only: bool) -> Iterator[dict[str, Any]]:
    coll = get_invoices_collection()
    for doc in coll.find({}, sort=[("created_at", -1)]):
        gemini_json = _extract_gemini_json(doc)
        additional = gemini_json.get("additional_fields") or {}
        if not isinstance(additional, dict):
            continue
        if not _hitl_flagged(additional):
            continue
        if pending_only and _hitl_status_value(additional) != 1:
            continue
        yield doc


def rollup_hitl_invoices(*, pending_only: bool, list_limit: int = 500) -> InvoiceRollup:
    amount_sum = 0.0
    amount_count = 0
    count = 0
    invoices: list[dict[str, str]] = []

    for doc in iter_hitl_invoices(pending_only=pending_only):
        count += 1
        gemini_json = _extract_gemini_json(doc)
        amount = _invoice_amount(gemini_json)
        if amount is not None:
            amount_sum += amount
            amount_count += 1
        if len(invoices) < list_limit:
            invoices.append(_invoice_row(doc))

    return InvoiceRollup(
        count=count,
        amount_sum=amount_sum,
        amount_count=amount_count,
        invoices=invoices,
    )


def format_table(
    rows: list[dict[str, str]],
    columns: list[tuple[str, str]],
) -> str:
    if not rows:
        return "No matching invoices found."

    keys = [key for key, _ in columns]
    headers = [header for _, header in columns]
    widths = [len(header) for header in headers]
    for row in rows:
        for idx, key in enumerate(keys):
            widths[idx] = max(widths[idx], len(str(row.get(key, "—"))))

    def _line(cells: list[str]) -> str:
        return "| " + " | ".join(cell.ljust(widths[idx]) for idx, cell in enumerate(cells)) + " |"

    separator = "|-" + "-|-".join("-" * width for width in widths) + "-|"
    lines = [_line(headers), separator]
    for row in rows:
        lines.append(_line([str(row.get(key, "—")) for key in keys]))
    return "\n".join(lines)


def format_hitl_answer(
    rollup: InvoiceRollup,
    *,
    pending_only: bool,
    as_table: bool,
) -> str:
    scope = "pending HITL review" if pending_only else "HITL flagged"
    if rollup.count == 0:
        return f"No invoice(s) are {scope} in the full database."

    summary_lines = [
        f"{rollup.count} invoice(s) are {scope} across the full database.",
        f"Combined total amount (parseable): {rollup.amount_sum:,.2f} "
        f"across {rollup.amount_count} invoice(s)",
    ]

    if not as_table:
        return "\n".join(summary_lines)

    columns = list(STANDARD_INVOICE_COLUMNS)
    if not pending_only:
        columns.append(("hitl_status", "Review status"))

    table = format_table(rollup.invoices, columns)
    remaining = rollup.count - len(rollup.invoices)
    lines = summary_lines + ["", table]
    if remaining > 0:
        lines.append(f"\n… and {remaining} more invoice(s) not shown.")
    return "\n".join(lines)


def answer_hitl_analytics(question: str, *, as_table: bool | None = None) -> str | None:
    if not wants_hitl_query(question):
        return None

    pending_only = hitl_pending_only(question)
    show_table = wants_invoice_list(question) if as_table is None else as_table
    rollup = rollup_hitl_invoices(pending_only=pending_only)
    return format_hitl_answer(rollup, pending_only=pending_only, as_table=show_table)


_RECENT_RE = re.compile(
    r"(recent|latest|last).{0,20}(invoice|invoices)",
    re.IGNORECASE,
)


def wants_recent_query(question: str) -> bool:
    return bool(_RECENT_RE.search(question.strip()))


def fetch_recent_invoices(limit: int = 8) -> list[dict[str, str]]:
    coll = get_invoices_collection()
    rows: list[dict[str, str]] = []
    for doc in coll.find({}, sort=[("_id", -1)], limit=limit):
        rows.append(_invoice_table_row(doc))
    return rows


def format_recent_invoices_answer(
    rows: list[dict[str, str]],
    *,
    limit: int,
    as_table: bool,
) -> str:
    if not rows:
        return "No invoices found in the database."

    header = f"Here are the {len(rows)} most recent invoices in the database:"
    if not as_table:
        lines = [header, ""]
        for row in rows:
            lines.append(
                f"• {row['invoice_number']} | {row['seller']} | "
                f"{row['invoice_date']} | Total: {row['total_amount']}"
            )
        return "\n".join(lines)

    table = format_table(rows, STANDARD_INVOICE_COLUMNS)
    return f"{header}\n\n{table}"


def answer_recent_invoices(question: str, *, limit: int = 8, as_table: bool | None = None) -> str | None:
    if not wants_recent_query(question):
        return None
    show_table = True if as_table is None else as_table
    rows = fetch_recent_invoices(limit=limit)
    return format_recent_invoices_answer(rows, limit=limit, as_table=show_table)


_INVOICE_WORD_RE = re.compile(r"\b(?:invoice|invoices|inovie\w*)\b", re.IGNORECASE)
_FIELD_LABELS: dict[str, str] = {
    "seller": r"seller|vendor",
    "buyer": r"buyer|customer|client",
    "invoice_number": r"invoice\s*(?:number|no\.?|#)|\binv\s*#",
}


def _clean_search_term(raw: str) -> str:
    term = raw.strip().strip("?.!,;:\"'")
    term = re.sub(r"\s+(invoice|invoices|related|matching|please)$", "", term, flags=re.IGNORECASE)
    return term.strip()


def parse_field_filter(question: str) -> tuple[str, str] | None:
    """Extract a seller/buyer/invoice_number filter from natural-language queries."""
    q = question.strip()
    if not q:
        return None

    if _INVOICE_WORD_RE.search(q):
        for field, label_pattern in _FIELD_LABELS.items():
            if field == "invoice_number":
                continue
            if not re.search(rf"\b(?:{label_pattern})\b", q, re.IGNORECASE):
                continue
            match = re.search(
                rf"(?:{label_pattern})\s*[:\-]?\s*(.+?)(?:\?|$|\.|,)",
                q,
                re.IGNORECASE,
            )
            if match:
                term = _clean_search_term(match.group(1))
                if len(term) >= 2:
                    return field, term

        number_match = re.search(
            r"(?:invoice\s*(?:number|no\.?|#)|inv\s*#)\s*[:\-]?\s*(.+?)(?:\?|$|\.|,)",
            q,
            re.IGNORECASE,
        )
        if number_match:
            term = _clean_search_term(number_match.group(1))
            if len(term) >= 2:
                return "invoice_number", term

    return None


def _dedupe_invoice_rows(rows: list[dict[str, str]]) -> list[dict[str, str]]:
    seen_numbers: set[str] = set()
    unique: list[dict[str, str]] = []
    for row in rows:
        invoice_number = str(row.get("invoice_number") or "").strip()
        if invoice_number and invoice_number != "—":
            if invoice_number in seen_numbers:
                continue
            seen_numbers.add(invoice_number)
        unique.append(row)
    return unique


def search_invoices_by_field(field: str, term: str, *, limit: int = 100) -> list[dict[str, str]]:
    coll = get_invoices_collection()
    term = term.strip()
    if len(term) < 2:
        return []

    if field == "invoice_number":
        paths = ["gemini.json.invoice_number", "gemini.json.invoice"]
    elif field == "buyer":
        paths = ["gemini.json.buyer", "gemini.json.customer"]
    else:
        paths = ["gemini.json.seller"]

    regex = re.compile(re.escape(term), re.IGNORECASE)
    clause = [{path: {"$regex": regex}} for path in paths]
    query = {"$or": clause} if len(clause) > 1 else clause[0]

    rows: list[dict[str, str]] = []
    for doc in coll.find(query, sort=[("created_at", -1)], limit=limit):
        rows.append(_invoice_table_row(doc))
    return _dedupe_invoice_rows(rows)


def format_invoice_search_answer(
    *,
    field: str,
    term: str,
    rows: list[dict[str, str]],
) -> str:
    labels = {
        "seller": "seller",
        "buyer": "buyer",
        "invoice_number": "invoice number",
    }
    label = labels.get(field, field.replace("_", " "))
    if not rows:
        return f'No invoices found where {label} matches "{term}".'

    amount_sum = 0.0
    amount_count = 0
    for row in rows:
        parsed = _parse_amount(row.get("total_amount"))
        if parsed is not None:
            amount_sum += parsed
            amount_count += 1

    summary = f'Found {len(rows)} invoice(s) where {label} matches "{term}".'
    if amount_count:
        summary += (
            f"\nCombined total amount (parseable): {amount_sum:,.2f} "
            f"across {amount_count} invoice(s)."
        )
    table = format_table(rows, STANDARD_INVOICE_COLUMNS)
    return f"{summary}\n\n{table}"


def answer_field_search(question: str) -> str | None:
    parsed = parse_field_filter(question)
    if parsed is None:
        return None
    field, term = parsed
    rows = search_invoices_by_field(field, term)
    return format_invoice_search_answer(field=field, term=term, rows=rows)


def rows_from_source_ids(source_ids: list[str]) -> list[dict[str, str]]:
    from bson import ObjectId
    from bson.errors import InvalidId

    coll = get_invoices_collection()
    rows: list[dict[str, str]] = []
    seen_ids: set[str] = set()
    for source_id in source_ids:
        sid = str(source_id).strip()
        if not sid or sid in seen_ids:
            continue
        seen_ids.add(sid)
        try:
            doc = coll.find_one({"_id": ObjectId(sid)})
        except (InvalidId, TypeError):
            doc = None
        if doc:
            rows.append(_invoice_table_row(doc))
    return _dedupe_invoice_rows(rows)


def format_semantic_search_answer(question: str, source_ids: list[str]) -> str:
    rows = rows_from_source_ids(source_ids)
    if not rows:
        return (
            "I could not find matching invoices in the full database. "
            "Try a specific filter, e.g. 'invoices for seller Maruti'."
        )

    parsed = parse_field_filter(question)
    if parsed:
        field, term = parsed
        filtered = [
            row
            for row in rows
            if re.search(re.escape(term), str(row.get(field if field != "invoice_number" else "invoice_number") or ""), re.IGNORECASE)
        ]
        if filtered:
            rows = filtered

    summary = f"Found {len(rows)} relevant invoice(s) from semantic search:"
    table = format_table(rows, STANDARD_INVOICE_COLUMNS)
    return f"{summary}\n\n{table}"
