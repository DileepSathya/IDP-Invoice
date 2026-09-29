import os
import sys
from pathlib import Path
from dotenv import load_dotenv

# Load .env from the repo root (infrastructure only — API key lives in MongoDB).
_root = Path(__file__).resolve().parent.parent
load_dotenv(_root / ".env")

# ── MongoDB ───────────────────────────────────────────────────────────────────
MONGO_URI        = os.getenv("MONGO_URI", "mongodb://localhost:27017")
MONGO_DB         = os.getenv("MONGO_DB", "IDP")
MONGO_COLLECTION = os.getenv("MONGO_INVOICES_COLLECTION", "invoices")

# ── Gemini ────────────────────────────────────────────────────────────────────
def _load_gemini_api_key() -> str:
    try:
        if str(_root) not in sys.path:
            sys.path.insert(0, str(_root))
        from backend.agent_settings import read_gemini_api_key

        return read_gemini_api_key()
    except Exception:
        return ""


GEMINI_API_KEY = _load_gemini_api_key()
GEMINI_MODEL     = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

# ── App ───────────────────────────────────────────────────────────────────────
MAX_LIST_RESULTS = 10          # max documents returned in a "list" query
APP_TITLE        = "Invoice Assistant"
