"""
Portable launcher: starts bundled MongoDB (optional), watcher, API, and opens the browser.

Built as: dist/IDP-Invoice/Start IDP Invoice.exe
"""

from __future__ import annotations

import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from urllib.parse import urlparse


def portable_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def ensure_layout(root: Path) -> None:
    for name in (
        "invoices_data",
        "invoices_data/to_be_processed",
        "invoices_data/_api_staging",
        "invoices_data/HITL_pending",
        "invoices_data/ERROR",
        "invoices_data/gemini_api_error",
        "invoices_data/Completed",
        "logs",
        "data/db",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)

    env_path = root / ".env"
    example_path = root / ".env.example"
    if not env_path.exists() and example_path.exists():
        shutil.copy(example_path, env_path)
        print(f"Created {env_path} from .env.example — set GEMINI_API_KEY before processing invoices.")

    tally_bridge_dir = root / "tally-bridge"
    tally_bridge_dir.mkdir(parents=True, exist_ok=True)
    tally_env = tally_bridge_dir / ".env"
    tally_example = tally_bridge_dir / ".env.example"
    if not tally_env.exists() and tally_example.exists():
        shutil.copy(tally_example, tally_env)
        print(f"Created {tally_env} from .env.example — set TALLY_COMPANY before pushing to Tally.")


def _read_env_value(root: Path, key: str, default: str) -> str:
    env_path = root / ".env"
    if not env_path.is_file():
        return default
    pattern = re.compile(rf"^\s*{re.escape(key)}\s*=\s*(.*)\s*$")
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if line.strip().startswith("#"):
            continue
        match = pattern.match(line)
        if not match:
            continue
        value = match.group(1).strip().strip('"').strip("'")
        return value or default
    return default


def _mongo_port_from_uri(uri: str) -> int:
    parsed = urlparse(uri)
    if parsed.port:
        return parsed.port
    return 27017


def _use_bundled_mongo(root: Path) -> tuple[bool, int]:
    if os.environ.get("IDP_USE_BUNDLED_MONGO", "1").strip().lower() in {"0", "false", "no"}:
        return False, 27017

    uri = _read_env_value(root, "MONGO_URI", "mongodb://localhost:27017")
    lowered = uri.lower()
    if "mongodb+srv://" in lowered:
        return False, _mongo_port_from_uri(uri)
    if "localhost" not in lowered and "127.0.0.1" not in lowered:
        return False, _mongo_port_from_uri(uri)

    mongod = root / "mongodb" / "bin" / "mongod.exe"
    if not mongod.is_file():
        return False, _mongo_port_from_uri(uri)

    return True, _mongo_port_from_uri(uri)


def _port_open(host: str, port: int, timeout: float = 1.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def wait_for_mongo(port: int, timeout: float = 120.0) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open("127.0.0.1", port):
            return True
        time.sleep(1.0)
    return False


def wait_for_api(port: int, timeout: float = 180.0) -> bool:
    url = f"http://127.0.0.1:{port}/invoices"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(1.0)
    return False


def wait_for_tally_bridge(port: int, timeout: float = 60.0) -> bool:
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status < 500:
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            time.sleep(1.0)
    return False


def _tally_enabled(root: Path) -> bool:
    return _read_env_value(root, "TALLY_ENABLED", "false").strip().lower() in {
        "true",
        "1",
        "yes",
        "on",
    }


def _popen_cmd(cmd: list[str], cwd: Path) -> subprocess.Popen:
    creationflags = 0
    if sys.platform == "win32":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        creationflags=creationflags,
    )


def start_bundled_mongo(root: Path, port: int) -> subprocess.Popen | None:
    mongod = root / "mongodb" / "bin" / "mongod.exe"
    dbpath = root / "data" / "db"
    logpath = root / "logs" / "mongod.log"

    if _port_open("127.0.0.1", port):
        print(f"MongoDB already listening on port {port} — using existing instance.")
        return None

    print(f"Starting bundled MongoDB on port {port} ...")
    cmd = [
        str(mongod),
        "--dbpath",
        str(dbpath),
        "--port",
        str(port),
        "--bind_ip",
        "127.0.0.1",
        "--logpath",
        str(logpath),
        "--logappend",
    ]
    proc = _popen_cmd(cmd, mongod.parent)
    if wait_for_mongo(port):
        print("MongoDB is ready.")
        return proc

    print("[WARN] Bundled MongoDB did not become ready in time. Check logs/mongod.log")
    return proc


def main() -> None:
    from license_validator import get_license_welcome_message, validate_license

    root = portable_root()
    os.chdir(root)
    validate_license()
    print()
    print(get_license_welcome_message())
    print()
    ensure_layout(root)

    api_port = int(os.environ.get("IDP_API_PORT", _read_env_value(root, "IDP_API_PORT", "8000")))
    tally_bridge_port = int(
        os.environ.get("TALLY_BRIDGE_PORT", _read_env_value(root, "TALLY_BRIDGE_PORT", "8001"))
    )
    api_exe = root / "idp-api" / "idp-api.exe"
    watcher_exe = root / "idp-watcher" / "idp-watcher.exe"
    tally_bridge_exe = root / "tally-bridge" / "tally-bridge.exe"

    if not api_exe.is_file():
        print(f"[ERROR] API executable not found: {api_exe}")
        input("Press Enter to exit.")
        sys.exit(1)

    processes: list[subprocess.Popen] = []
    mongo_proc: subprocess.Popen | None = None
    try:
        use_bundled, mongo_port = _use_bundled_mongo(root)
        if use_bundled:
            mongo_proc = start_bundled_mongo(root, mongo_port)
            if mongo_proc is not None:
                processes.append(mongo_proc)
        else:
            print("Using external MongoDB from MONGO_URI (bundled MongoDB skipped).")

        if watcher_exe.is_file():
            print(f"Starting watcher: {watcher_exe}")
            processes.append(_popen_cmd([str(watcher_exe)], root))
        else:
            print(f"[WARN] Watcher not found (skipping): {watcher_exe}")

        if _tally_enabled(root):
            if tally_bridge_exe.is_file():
                print(f"Starting Tally bridge: {tally_bridge_exe}")
                processes.append(_popen_cmd([str(tally_bridge_exe)], root / "tally-bridge"))
                print(f"Waiting for Tally bridge on port {tally_bridge_port}...")
                if wait_for_tally_bridge(tally_bridge_port):
                    print("Tally bridge is ready.")
                else:
                    print("[WARN] Tally bridge did not respond in time. ERP push may fail until it is up.")
            else:
                print(f"[WARN] Tally enabled but bridge not found (skipping): {tally_bridge_exe}")

        print(f"Starting API: {api_exe}")
        processes.append(_popen_cmd([str(api_exe)], root))

        print(f"Waiting for API on port {api_port}...")
        if wait_for_api(api_port):
            url = f"http://127.0.0.1:{api_port}/"
            print(f"Opening {url}")
            webbrowser.open(url)
        else:
            print("[WARN] API did not respond in time. Check logs/idp.log")

        print()
        print("IDP Invoice is running. Close this window or press Enter to stop all services.")
        input()
    except KeyboardInterrupt:
        pass
    finally:
        for proc in reversed(processes):
            proc.terminate()
        for proc in reversed(processes):
            try:
                proc.wait(timeout=15)
            except subprocess.TimeoutExpired:
                proc.kill()


if __name__ == "__main__":
    main()
