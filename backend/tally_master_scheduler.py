"""Background scheduler for Tally master-data refresh.

Started from the FastAPI lifespan (backend/api.py). Every
`_CHECK_INTERVAL_SECONDS` it checks persisted scheduler settings
(backend/tally_master_settings.py); when mode is interval-based ("scheduled")
or time-based ("time_based") and a run is due, it triggers a Tally master
refresh (backend/tally_master_sync.py). Manual refresh via the Settings UI is
unaffected.

Uses the same polling pattern as backend/erp_scheduler.py so saving new
settings from the frontend takes effect within _CHECK_INTERVAL_SECONDS.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 30

_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _loop() -> None:
    from backend import tally_master_settings
    from backend.tally_master_sync import run_tally_master_refresh_async

    logger.info(
        "[tally_master_scheduler] Background refresh scheduler started (checking every %ss).",
        _CHECK_INTERVAL_SECONDS,
    )
    while not _stop_event.wait(_CHECK_INTERVAL_SECONDS):
        try:
            settings = tally_master_settings.get_tally_master_scheduler_settings()
            if not tally_master_settings.due_for_scheduled_refresh(settings):
                continue

            due_slots = (
                tally_master_settings.get_due_time_slots(settings)
                if settings["mode"] == "time_based"
                else []
            )
            if settings["mode"] == "scheduled":
                logger.info(
                    "[tally_master_scheduler] Scheduled refresh due (every %s min) — running now.",
                    settings["frequency_minutes"],
                )
            elif due_slots:
                logger.info(
                    "[tally_master_scheduler] Time-based refresh due at %s (%s) — running now.",
                    ", ".join(due_slots),
                    settings.get("timezone"),
                )

            started = run_tally_master_refresh_async(
                rematch_invoices=bool(settings.get("rematch_after_scheduled_refresh")),
            )
            if started and due_slots:
                tally_master_settings.mark_time_slots_run(due_slots)
        except Exception:
            logger.exception("[tally_master_scheduler] Scheduler tick failed; will retry next interval.")


def start_tally_master_scheduler() -> None:
    """Idempotent — safe to call multiple times; only the first call starts the thread."""
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(
        target=_loop,
        name="tally-master-scheduler",
        daemon=True,
    )
    _thread.start()


def stop_tally_master_scheduler() -> None:
    _stop_event.set()
