import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the parent folder (D:\IDP_2.0-INVOICE\.env)
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# ── MongoDB ───────────────────────────────────────────────────────────────────
MONGO_URI        = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB         = os.getenv("MONGO_DB", "IDP")
MONGO_COLLECTION = os.getenv("MONGO_INVOICES_COLLECTION", "invoices")

# ── Gemini ────────────────────────────────────────────────────────────────────
GEMINI_API_KEY   = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ── App ───────────────────────────────────────────────────────────────────────
MAX_LIST_RESULTS = 10          # max documents returned in a "list" query
APP_TITLE        = "Invoice Assistant"
