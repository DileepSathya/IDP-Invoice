"""Fetch Tally expense-group ledgers (Direct Expenses, Indirect Expenses,
Misc. Expenses (ASSET)) for ERP matching, including any custom sub-groups
nested under each, at any depth.

Mirrors masters.py's fetch_vendors() Sundry-Creditors approach, but for three
disjoint root groups instead of one - Direct Expenses, Indirect Expenses, and
Misc. Expenses (ASSET) are separate Tally primary groups, not one shared
tree, and Tally's TDL <CHILDOF> attribute only accepts a single group name
per request. So each root is fetched with its own
<CHILDOF>...</CHILDOF><BELONGSTO>Yes</BELONGSTO> request (same mechanism
masters.py uses for Sundry Creditors), and the results are merged and
deduplicated by ledger name - Tally enforces globally-unique ledger names
within a company, so a name-based dedupe is safe even if two root groups'
trees were ever to overlap.
"""

from __future__ import annotations

import logging
from typing import Any, Optional

import xml.etree.ElementTree as ET

from tally.client import load_xml_template, post_tally
from tally.configurations.config import clean_tally_xml

logger = logging.getLogger(__name__)

# Actual (canonical) Tally primary-group names whose full sub-group trees
# count as expense ledgers. These must be the real object names, not any
# configured alias - e.g. "Direct Expenses" has the alias "Expenses (Direct)"
# in some installs, but Tally's CHILDOF match is against the canonical name,
# so the alias would not match here.
EXPENSE_ROOT_GROUPS: tuple[str, ...] = (
    "Direct Expenses",
    "Indirect Expenses",

)

_CHILDOF_PLACEHOLDER = "{{CHILDOF_GROUP}}"


def _ledger_gstin(ledger: ET.Element) -> Optional[str]:
    for tag in ("GSTIN", "PARTYGSTIN", "INCOMETAXNUMBER"):
        node = ledger.find(tag)
        if node is not None and node.text and node.text.strip():
            return node.text.strip()
    for node in ledger.iter():
        if node.tag.upper() in {"GSTIN", "PARTYGSTIN"} and node.text and node.text.strip():
            return node.text.strip()
    return None


def _fetch_ledgers_under(
    tally_url: str,
    root_group: str,
    *,
    company_name: Optional[str],
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Fetch every ledger under `root_group`, including nested custom
    sub-groups at any depth, via expense_ledger.xml's CHILDOF/BELONGSTO."""
    try:
        xml_request = load_xml_template("expense_ledger.xml", company_name=company_name)
    except FileNotFoundError as exc:
        return [], str(exc)

    xml_request = xml_request.replace(_CHILDOF_PLACEHOLDER, root_group)

    raw_text, error = post_tally(tally_url, xml_request)
    if error:
        return [], f"{root_group}: {error}"

    cleaned = clean_tally_xml(raw_text)
    try:
        root = ET.fromstring(cleaned)
    except ET.ParseError as exc:
        return [], f"Invalid XML from Tally (expenses under '{root_group}'): {exc}"

    rows: list[dict[str, Any]] = []
    for ledger in root.findall(".//LEDGER"):
        name = (ledger.get("NAME") or "").strip()
        if not name:
            name_tag = ledger.find("NAME")
            if name_tag is not None and name_tag.text:
                name = name_tag.text.strip()
        if not name:
            continue

        # Immediate parent group (root_group itself, or a custom sub-group
        # nested under it). Kept for auditing only, not used as a filter -
        # the Tally-side CHILDOF/BELONGSTO request already guarantees every
        # ledger here rolls up to root_group.
        parent_tag = ledger.find("PARENT")
        parent = parent_tag.text.strip() if parent_tag is not None and parent_tag.text else ""

        gst = _ledger_gstin(ledger)
        rows.append(
            {
                "expense_ledger_id": name,
                "expense_ledger_name": name,
                # Which of EXPENSE_ROOT_GROUPS this ledger resolved under -
                # Direct / Indirect / Misc. Expenses (ASSET) are accounted
                # differently downstream, so this is worth keeping distinct
                # from the immediate parent group below.
                "expense_root_group": root_group,
                "expense_group": parent or None,
                "gst_tax_number": gst,
                "tin_number": None,
                "vendor_address_1": None,
                "vendor_address_2": None,
                "vendor_address_3": None,
                "city": None,
                "state": None,
                "country": None,
                "pin_code": None,
                "primary_ph_number": None,
                "email_primary": None,
                "bank_ac_number": None,
                "bank_name": None,
            }
        )

    return rows, None


def fetch_expense_ledgers(
    tally_url: str,
    *,
    company_name: Optional[str] = None,
    root_groups: tuple[str, ...] = EXPENSE_ROOT_GROUPS,
) -> tuple[list[dict[str, Any]], Optional[str]]:
    """Return every ledger under any of `root_groups` (Direct Expenses,
    Indirect Expenses, Misc. Expenses (ASSET) by default), including ledgers
    nested under custom sub-groups at any depth.

    Each root group is fetched with its own Tally request and the results are
    merged and deduplicated by ledger name. A failure fetching one root group
    doesn't block the others - partial results are returned along with an
    aggregated error string describing what failed, mirroring
    fetch_all_masters()'s error-collection style in masters.py.
    """
    merged: dict[str, dict[str, Any]] = {}
    errors: list[str] = []

    for root_group in root_groups:
        rows, error = _fetch_ledgers_under(tally_url, root_group, company_name=company_name)
        if error:
            errors.append(error)
            continue
        for row in rows:
            merged.setdefault(row["expense_ledger_name"], row)

    combined = sorted(merged.values(), key=lambda row: (row.get("expense_ledger_name") or "").lower())
    error_summary = "; ".join(errors) if errors else None
    return combined, error_summary