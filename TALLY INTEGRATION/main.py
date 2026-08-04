"""
Manual CLI — push invoice(s) via voucher-only pipeline (no validation).

Usage (from this directory):
    python main.py
"""

from __future__ import annotations

import logging
import os

from dotenv import load_dotenv
from logging_config import setup_logging

load_dotenv()
setup_logging()

from tally.pipeline import push_invoice_document
from tally import invoice_data_retriver

logger = logging.getLogger(__name__)

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "IDP")
MONGO_COLLECTION = os.environ.get("MONGO_INVOICES_COLLECTION", "invoices")

INVOICE_NUMBER = os.environ.get("INVOICE_NUMBER", "KLKA2526-12078").strip()


def main() -> None:
    if not INVOICE_NUMBER:
        logger.error("INVOICE_NUMBER is not set.")
        return

    doc = invoice_data_retriver.db_connection(MONGO_URI, MONGO_DB, MONGO_COLLECTION, INVOICE_NUMBER)
    result = push_invoice_document(doc)

    if result.get("success"):
        logger.info("Success: %s", result.get("message"))
    else:
        logger.error("Failed: %s", result.get("error_reason") or result.get("message"))


if __name__ == "__main__":
    main()
