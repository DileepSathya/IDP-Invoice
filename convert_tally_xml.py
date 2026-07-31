"""
CLI wrapper — pushes an invoice via the Tally bridge service.

Usage:
  Set INVOICE_NUMBER in env or below, ensure bridge is running, then:
      python convert_tally_xml.py
"""

from __future__ import annotations

import os
import sys

import requests
from dotenv import load_dotenv

load_dotenv()

INVOICE_NUMBER = os.environ.get("INVOICE_NUMBER", "").strip()
TALLY_BRIDGE_URL = os.environ.get("TALLY_BRIDGE_URL", "http://localhost:8001").strip().rstrip("/")


def main() -> None:
    if not INVOICE_NUMBER:
        print("INVOICE_NUMBER is not set.", file=sys.stderr)
        sys.exit(1)

    from backend.agents.database import get_invoices_collection

    coll = get_invoices_collection()
    doc = coll.find_one(
        {
            "$or": [
                {"gemini.json.invoice_number": INVOICE_NUMBER},
                {"gemini.json.invoice": INVOICE_NUMBER},
            ]
        },
        sort=[("_id", -1)],
    )
    if not doc:
        print(f"No invoice found for '{INVOICE_NUMBER}'.", file=sys.stderr)
        sys.exit(1)

    invoice_id = str(doc["_id"])
    url = f"{TALLY_BRIDGE_URL}/push/invoice/{invoice_id}?force=true"
    try:
        resp = requests.post(url, timeout=120)
    except requests.RequestException as exc:
        print(f"Bridge unreachable at {TALLY_BRIDGE_URL}: {exc}", file=sys.stderr)
        sys.exit(1)

    if resp.status_code >= 400:
        print(resp.text, file=sys.stderr)
        sys.exit(1)

    result = resp.json()
    if result.get("success"):
        print(f"Success: {result.get('message')} (invoice {result.get('invoice_number')})")
        sys.exit(0)

    print(f"Failed: {result.get('error_reason') or result.get('message')}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
