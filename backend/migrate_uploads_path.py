"""
Migration script: update uploaded_file_path in MongoDB when invoice files moved folders.

Maps legacy paths (uploads/, raw/, error_files/) to invoices_data/Completed, ERROR, etc.
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

from backend.invoice_files import COMPLETED_DIR, ERROR_DIR, HITL_PENDING_DIR, resolve_invoice_file

load_dotenv()

MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "IDP")
MONGO_INVOICES_COLLECTION = os.environ.get("MONGO_INVOICES_COLLECTION", "invoices")

client = MongoClient(MONGO_URI)
coll = client[MONGO_DB][MONGO_INVOICES_COLLECTION]

print(f"MongoDB: {MONGO_URI} / {MONGO_DB}.{MONGO_INVOICES_COLLECTION}")
print(f"Completed dir: {COMPLETED_DIR}")
print(f"HITL pending dir: {HITL_PENDING_DIR}")
print(f"ERROR dir: {ERROR_DIR}")

updated = 0
missing = 0

for doc in coll.find({}, {"uploaded_file_path": 1, "file_path": 1}):
    for field in ("uploaded_file_path", "file_path"):
        raw = doc.get(field)
        if not raw:
            continue
        path = Path(raw)
        if path.is_file():
            continue
        resolved = resolve_invoice_file(path.name)
        if resolved is None:
            missing += 1
            print(f"[MISSING] {doc.get('_id')} {field}={raw}")
            continue
        new_path = str(resolved)
        if new_path != raw:
            coll.update_one({"_id": doc["_id"]}, {"$set": {field: new_path}})
            updated += 1
            print(f"[UPDATED] {doc.get('_id')} {field} -> {new_path}")

print(f"Done. updated={updated} missing_on_disk={missing}")
