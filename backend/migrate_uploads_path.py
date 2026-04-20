#!/usr/bin/env python3
"""
Migration script to update uploaded_file_path in MongoDB to use the new UPLOADS_DIR.
"""
import os
from pathlib import Path

from dotenv import load_dotenv
from pymongo import MongoClient

# Load .env from project root (one level above backend/)
ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

# Get configuration
MONGO_URI = os.environ.get("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB = os.environ.get("MONGO_DB", "IDP")
MONGO_INVOICES_COLLECTION = os.environ.get("MONGO_INVOICES_COLLECTION", "invoices")
UPLOADS_DIR = Path(os.environ.get("UPLOADS_DIR", "./invoices_data/uploads")).expanduser().resolve()

print(f"Connecting to MongoDB: {MONGO_URI}")
print(f"Database: {MONGO_DB}")
print(f"Collection: {MONGO_INVOICES_COLLECTION}")
print(f"New UPLOADS_DIR: {UPLOADS_DIR}")
print()

try:
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client[MONGO_DB]
    coll = db[MONGO_INVOICES_COLLECTION]

    # Find all documents with uploaded_file_path
    docs = list(coll.find({"uploaded_file_path": {"$exists": True, "$ne": None}}))

    if not docs:
        print("No documents with uploaded_file_path found.")
    else:
        print(f"Found {len(docs)} documents with uploaded_file_path.\n")

        updated_count = 0
        for doc in docs:
            old_path = doc.get("uploaded_file_path")

            # Extract just the filename from the old path
            filename = Path(old_path).name

            # Create new full path
            new_path = str(UPLOADS_DIR / filename)

            if old_path != new_path:
                print(f"Updating: {filename}")
                print(f"  Old: {old_path}")
                print(f"  New: {new_path}")

                # Update the document
                coll.update_one(
                    {"_id": doc["_id"]},
                    {"$set": {"uploaded_file_path": new_path}},
                )
                updated_count += 1
                print()

        print(f"\nSuccessfully updated {updated_count} documents.")

    client.close()
    print("Migration complete!")

except Exception as e:
    print(f"Error: {e}")
    import traceback

    traceback.print_exc()
