"""Portable / dev entry point for the Tally bridge (TALLY INTEGRATION FastAPI service)."""

from __future__ import annotations

import multiprocessing
import os
import sys
from pathlib import Path


def _tally_bridge_root() -> Path:
    from backend.app_paths import app_dir, is_frozen, repo_root

    if is_frozen():
        return app_dir() / "tally-bridge"
    return repo_root() / "TALLY INTEGRATION"


def _configure_bridge_env(bridge_root: Path) -> None:
    from dotenv import load_dotenv

    load_dotenv(bridge_root / ".env", override=False)

    template = bridge_root / "xml_scripts" / "create_voucher.xml"
    if template.is_file():
        os.environ.setdefault("TALLY_VOUCHER_TEMPLATE", str(template))

    # Pin the outgoing-payload dump to one predictable place. Left to its own
    # default it lands next to whichever bridge folder is active, so the source
    # run and the packaged build write to two different last_payload.xml files
    # and the one you happen to be watching looks like it stopped updating.
    from backend.app_paths import logs_dir

    os.environ.setdefault("TALLY_LAST_PAYLOAD_PATH", str(logs_dir() / "last_payload.xml"))

    if str(bridge_root) not in sys.path:
        sys.path.insert(0, str(bridge_root))


def main() -> None:
    bridge_root = _tally_bridge_root()
    if not bridge_root.is_dir():
        raise SystemExit(f"Tally bridge folder not found: {bridge_root}")

    _configure_bridge_env(bridge_root)
    os.chdir(bridge_root)

    import uvicorn

    import api_server  # noqa: F401 — bundled by PyInstaller

    port = int(os.environ.get("TALLY_BRIDGE_PORT", "8001"))
    uvicorn.run(api_server.app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    main()
