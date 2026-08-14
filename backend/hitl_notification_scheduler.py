"""Background scheduler for HITL scheduled digest emails.

Started once from the FastAPI app's lifespan (backend/api.py). Every
`_CHECK_INTERVAL_SECONDS` it checks persisted notification settings and
sends a digest when mode is "scheduled_digest" and the configured frequency
has elapsed.
"""

from __future__ import annotations

import logging
import threading

logger = logging.getLogger(__name__)

_CHECK_INTERVAL_SECONDS = 30

_thread: threading.Thread | None = None
_stop_event = threading.Event()


def _loop() -> None:
    from backend.hitl_notifications import maybe_send_scheduled_digest

    logger.info(
        "[hitl_notification_scheduler] Started (checking every %ss).",
        _CHECK_INTERVAL_SECONDS,
    )
    while not _stop_event.wait(_CHECK_INTERVAL_SECONDS):
        try:
            maybe_send_scheduled_digest()
        except Exception:
            logger.exception(
                "[hitl_notification_scheduler] Scheduler tick failed; will retry next interval."
            )


def start_hitl_notification_scheduler() -> None:
    global _thread
    if _thread is not None and _thread.is_alive():
        return
    _stop_event.clear()
    _thread = threading.Thread(
        target=_loop, name="hitl-notification-scheduler", daemon=True
    )
    _thread.start()


def stop_hitl_notification_scheduler() -> None:
    _stop_event.set()
