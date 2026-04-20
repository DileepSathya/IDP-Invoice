"""
Agent utilities package.

Keep this module import-light:
- Importing `backend.agents` should not trigger heavy OCR/LLM dependencies.
- Import the concrete modules directly (e.g. `backend.agents.ocr`, `backend.agents.database`).
"""

# Database helpers are optional at import-time (watcher should still start without Mongo deps).
try:
    from .database import get_db, get_invoices_collection, store_invoice_result, store_ocr_result  # noqa: F401
except Exception:
    pass

