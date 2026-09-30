"""PyInstaller / portable entry point for the FastAPI server."""

from __future__ import annotations

import multiprocessing
import os
import sys

from backend.app_paths import app_dir, load_app_dotenv

load_app_dotenv()


def main() -> None:
    if "--shutdown-services" in sys.argv:
        from backend.shutdown_service import stop_registered_services

        stop_registered_services(app_dir())
        return

    import uvicorn

    from backend.api import app

    os.chdir(app_dir())
    port = int(os.environ.get("IDP_API_PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    multiprocessing.freeze_support()
    os.chdir(app_dir())
    main()
