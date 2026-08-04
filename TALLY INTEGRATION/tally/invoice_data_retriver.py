"""Fetch invoice documents from MongoDB."""

from __future__ import annotations

import logging
import sys
from typing import Any, Optional

from bson import ObjectId
from pymongo import MongoClient

logger = logging.getLogger(__name__)


def _collection(MONGO_URI: str, MONGO_DB: str, MONGO_COLLECTION: str):
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    client.server_info()
    return client[MONGO_DB][MONGO_COLLECTION]


def db_connection(MONGO_URI, MONGO_DB, MONGO_COLLECTION, invoice_number):
    try:
        coll = _collection(MONGO_URI, MONGO_DB, MONGO_COLLECTION)
        logger.info("Connected to DB '%s', collection '%s'", MONGO_DB, MONGO_COLLECTION)
    except Exception as exc:
        logger.error("MongoDB connection failed: %s", exc)
        sys.exit(1)

    logger.info("Searching for invoice '%s' ...", invoice_number)
    doc = coll.find_one(
        {
            "$or": [
                {"gemini.json.invoice_number": invoice_number},
                {"gemini.json.invoice": invoice_number},
            ]
        },
        sort=[("_id", -1)],
    )

    if not doc:
        logger.error("No data found for invoice number '%s'.", invoice_number)
        sys.exit(1)

    logger.info("Invoice found in MongoDB (document _id: %s)", doc.get("_id"))
    return doc


def fetch_by_id(MONGO_URI: str, MONGO_DB: str, MONGO_COLLECTION: str, invoice_id: str) -> Optional[dict[str, Any]]:
    try:
        oid = ObjectId(invoice_id)
    except Exception:
        return None

    try:
        coll = _collection(MONGO_URI, MONGO_DB, MONGO_COLLECTION)
    except Exception as exc:
        logger.error("MongoDB connection failed: %s", exc)
        return None

    return coll.find_one({"_id": oid})
