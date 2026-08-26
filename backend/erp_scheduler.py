"""Background scheduler for ERP re-syncing.

Started once from the FastAPI app's lifespan (backend/api.py). Every
`_CHECK_INTERVAL_SECONDS` it checks the persisted ERP settings
(backend/erp_settings.py); when mode is "scheduled" and the configured
frequency has elapsed since the last sync, it triggers a full re-sync
(backend/erp_sync.py). When mode is "immediate", this loop is effectively
idle - per-invoice matching at ingest time (backend/hitl_status.py) is all
that runs.

A lightweight polling thread (rather than one long sleep sized to the
configured frequency) so that saving a new frequency, or switching modes,
from the ERP Settings panel takes effect within _CHECK_INTERVAL_SECONDS
instead of waiting out whatever interval was previously in effect.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 30

_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _loop() -> None:
    from backend import erp_settings
    from backend.erp_sync import run_erp_sync

    logger.info("[erp_scheduler] Background ERP sync scheduler started (checking every %ss).", _CHECK_INTERVAL_SECONDS)
    while not _stop_event.wait(_CHECK_INTERVAL_SECONDS):
        try:
            settings = erp_settings.get_erp_sync_settings()
            if erp_settings.due_for_scheduled_sync(settings):
                logger.info(
                    "[erp_scheduler] Scheduled sync due (every %s min) - running now.",
                    settings["frequency_minutes"],
                )
                run_erp_sync()
        except Exception:
            logger.exception("[erp_scheduler] Scheduler tick failed; will retry next interval.")


def start_erp_scheduler() -> None:
    """Idempotent - safe to call multiple times (e.g. re-entrant lifespan in
    tests); only the first call actually starts the thread."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(target=_loop, name="erp-sync-scheduler", daemon=True)
    _thread.start()


def stop_erp_scheduler() -> None:
    _stop_event.set()
