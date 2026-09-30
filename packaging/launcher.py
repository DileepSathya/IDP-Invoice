"""
Portable launcher: starts bundled MongoDB, invoice watcher, Tally bridge, API, and opens the browser.

Built as: dist/IDP-Invoice/Start IDP Invoice.exe
"""

from __future__ import annotations

import os
import json
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


class StartupWindow:
    """Small native status window shown while background services start."""

    def __init__(self, root, status_label, detail_label=None, open_logs_button=None) -> None:
        self.root = root
        self.status_label = status_label
        self.detail_label = detail_label
        self.open_logs_button = open_logs_button

    def status(self, message: str) -> None:
        self.status_label.configure(text=message)
        self.root.update_idletasks()
        self.root.update()

    def show_error(self, message: str, detail: str) -> None:
        self.status(message)
        if self.detail_label is not None:
            self.detail_label.configure(text=detail)
        if self.open_logs_button is not None:
            self.open_logs_button.pack(pady=(12, 0))
        self.root.mainloop()

    def close(self) -> None:
        self.root.destroy()


def create_startup_window(root_path: Path) -> StartupWindow | None:
    """Create the user-facing progress window; never prevent startup if Tk fails."""
    try:
        import tkinter as tk

        window = tk.Tk()
        window.title("IDP Invoice")
        window.resizable(False, False)
        window.attributes("-topmost", True)
        frame = tk.Frame(window, padx=28, pady=24)
        frame.pack()
        tk.Label(frame, text="IDP Invoice", font=("Segoe UI", 15, "bold")).pack(anchor="w")
        status = tk.Label(frame, text="Starting IDP Invoice…", font=("Segoe UI", 10), anchor="w")
        status.pack(anchor="w", pady=(10, 0))
        detail = tk.Label(frame, text="Please wait. The web interface will open automatically.", anchor="w")
        detail.pack(anchor="w", pady=(5, 0))

        def open_logs() -> None:
            os.startfile(str(root_path / "logs"))

        open_logs_button = tk.Button(frame, text="Open logs", command=open_logs)
        window.update_idletasks()
        width, height = window.winfo_width(), window.winfo_height()
        x = (window.winfo_screenwidth() - width) // 2
        y = (window.winfo_screenheight() - height) // 3
        window.geometry(f"{width}x{height}+{x}+{y}")
        return StartupWindow(window, status, detail, open_logs_button)
    except Exception:
        return None


def _sync_env_from_example(env_path: Path, example_path: Path) -> list[str]:
    """Append keys from example that are missing in env. Returns added key names."""
    if not example_path.is_file():
        return []
    if not env_path.is_file():
        shutil.copy(example_path, env_path)
        return ["<created>"]
    existing_text = env_path.read_text(encoding="utf-8")
    existing_keys: set[str] = set()
    for line in existing_text.splitlines():
        match = re.match(r"^\s*([^#=]+?)=", line)
        if match:
            existing_keys.add(match.group(1).strip())
    added: list[str] = []
    to_append: list[str] = []
    for line in example_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = re.match(r"^\s*([^=]+?)=", line)
        if not match:
            continue
        key = match.group(1).strip()
        if key not in existing_keys:
            to_append.append(line)
            added.append(key)
    if to_append:
        with env_path.open("a", encoding="utf-8") as handle:
            if existing_text and not existing_text.endswith("\n"):
                handle.write("\n")
            handle.write("\n".join(to_append) + "\n")
    return added


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
    if example_path.is_file():
        added = _sync_env_from_example(env_path, example_path)
        if added == ["<created>"]:
            print(
                f"Created {env_path} from .env.example — set the Gemini API key under Settings → AI after login."
            )
        elif added:
            print(f"Added missing .env keys: {', '.join(added)}")

    tally_bridge_dir = root / "tally-bridge"
    tally_bridge_dir.mkdir(parents=True, exist_ok=True)
    (tally_bridge_dir / "xml_scripts").mkdir(parents=True, exist_ok=True)
    tally_env = tally_bridge_dir / ".env"
    tally_example = tally_bridge_dir / ".env.example"
    if tally_example.is_file():
        added = _sync_env_from_example(tally_env, tally_example)
        if added == ["<created>"]:
            print(
                f"Created {tally_env} from .env.example — set TALLY_COMPANY, "
                "TALLY_VOUCHER_TYPE, and TALLY_PURCHASE_LEDGER (or use Settings -> Ledger Settings)."
            )
        elif added:
            print(f"Added missing tally-bridge .env keys: {', '.join(added)}")


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
    url = f"http://127.0.0.1:{port}/health"
    deadline = time.time() + timeout
    attempt = 0
    while time.time() < deadline:
        attempt += 1
        try:
            with urllib.request.urlopen(url, timeout=3) as resp:
                if resp.status < 500:
                    if attempt > 1:
                        print(f"API is ready (checked /health after {attempt} attempt(s)).")
                    return True
        except (urllib.error.URLError, TimeoutError, OSError):
            if attempt == 1 or attempt % 10 == 0:
                print(f"Waiting for API on {url} ...")
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
        # The launcher is a short-lived GUI process.  A new process group and
        # CREATE_NO_WINDOW keep services running in the background without a
        # visible console.  Do not use DETACHED_PROCESS: it clears standard
        # handles and prevents the console-based API from initializing its
        # Uvicorn logging correctly.
        creationflags = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.CREATE_NO_WINDOW
        )
    return subprocess.Popen(
        cmd,
        cwd=str(cwd),
        creationflags=creationflags,
    )


def _pid_record_path(root: Path) -> Path:
    return root / "data" / "runtime" / "service-pids.json"


def _write_pid_record(root: Path, services: dict[str, tuple[subprocess.Popen, Path]]) -> None:
    path = _pid_record_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "services": [
            {"name": name, "pid": process.pid, "executable": str(executable.resolve())}
            for name, (process, executable) in services.items()
        ]
    }
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload), encoding="utf-8")
    temporary.replace(path)


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
    root = portable_root()
    os.chdir(root)
    ensure_layout(root)
    splash = create_startup_window(root)

    def update_status(message: str) -> None:
        if splash is not None:
            splash.status(message)

    api_port = int(os.environ.get("IDP_API_PORT", _read_env_value(root, "IDP_API_PORT", "8000")))
    tally_bridge_port = int(
        os.environ.get("TALLY_BRIDGE_PORT", _read_env_value(root, "TALLY_BRIDGE_PORT", "8001"))
    )
    api_exe = root / "idp-services" / "idp-api.exe"
    watcher_exe = root / "idp-services" / "idp-watcher.exe"
    tally_bridge_exe = root / "tally-bridge" / "tally-bridge.exe"

    if not api_exe.is_file():
        print(f"[ERROR] API executable not found: {api_exe}")
        if splash is not None:
            splash.show_error("IDP Invoice could not start", "The API executable is missing. Open logs for details.")
        sys.exit(1)

    if _port_open("127.0.0.1", api_port):
        update_status("IDP Invoice is already running — opening the web interface…")
        webbrowser.open(f"http://127.0.0.1:{api_port}/login")
        time.sleep(0.7)
        if splash is not None:
            splash.close()
        return

    processes: list[subprocess.Popen] = []
    started_services: dict[str, tuple[subprocess.Popen, Path]] = {}
    _pid_record_path(root).unlink(missing_ok=True)
    keep_services_running = False
    mongo_proc: subprocess.Popen | None = None
    try:
        use_bundled, mongo_port = _use_bundled_mongo(root)
        if use_bundled:
            update_status("Starting database…")
            mongo_proc = start_bundled_mongo(root, mongo_port)
            if mongo_proc is not None:
                processes.append(mongo_proc)
                started_services["mongo"] = (mongo_proc, root / "mongodb" / "bin" / "mongod.exe")
        else:
            print("Using external MongoDB from MONGO_URI (bundled MongoDB skipped).")

        if watcher_exe.is_file():
            update_status("Starting invoice watcher…")
            print(f"Starting watcher: {watcher_exe}")
            watcher_proc = _popen_cmd([str(watcher_exe)], root)
            processes.append(watcher_proc)
            started_services["watcher"] = (watcher_proc, watcher_exe)
        else:
            print(f"[WARN] Watcher not found (skipping): {watcher_exe}")

        if _tally_enabled(root):
            if tally_bridge_exe.is_file():
                update_status("Starting Tally bridge…")
                print(f"Starting Tally bridge: {tally_bridge_exe}")
                tally_proc = _popen_cmd([str(tally_bridge_exe)], root / "tally-bridge")
                processes.append(tally_proc)
                started_services["tally_bridge"] = (tally_proc, tally_bridge_exe)
                print(f"Waiting for Tally bridge on port {tally_bridge_port}...")
                if wait_for_tally_bridge(tally_bridge_port):
                    print("Tally bridge is ready.")
                else:
                    print("[WARN] Tally bridge did not respond in time. ERP push may fail until it is up.")
            else:
                print(f"[WARN] Tally enabled but bridge not found (skipping): {tally_bridge_exe}")
        else:
            print("[WARN] TALLY_ENABLED is false — ERP matching and Tally push are disabled.")

        update_status("Starting web server…")
        print(f"Starting API: {api_exe}")
        api_proc = _popen_cmd([str(api_exe)], root)
        processes.append(api_proc)
        started_services["api"] = (api_proc, api_exe)

        update_status("Preparing the web interface…")
        print(f"Waiting for API on port {api_port}...")
        if wait_for_api(api_port):
            try:
                from license_validator import get_license_welcome_message

                print()
                print(get_license_welcome_message())
                print()
            except Exception:
                pass
            url = f"http://127.0.0.1:{api_port}/login"
            update_status("Opening web interface…")
            print(f"Opening {url}")
            webbrowser.open(url)
            print(
                "After login, refresh Tally master data under Settings → Tally Master Data "
                "(manual Refresh or Scheduled mode with minutes). "
                "Then use Health to verify services."
            )
            print(
                "Use http://127.0.0.1:{0}/ (not localhost) so login sessions persist.".format(
                    api_port
                )
            )

            # The customer-facing executable exits now.  Its detached child
            # services remain available while the user works in the browser.
            _write_pid_record(root, started_services)
            keep_services_running = True
            time.sleep(0.7)
            if splash is not None:
                splash.close()
            return
        else:
            print("[WARN] API did not respond in time. Check logs/idp.log")
            if splash is not None:
                splash.show_error(
                    "IDP Invoice could not start",
                    "The web server did not respond. Open logs for details.",
                )
    except KeyboardInterrupt:
        pass
    except Exception as exc:
        print(f"[ERROR] IDP Invoice startup failed: {exc}")
        if splash is not None:
            splash.show_error("IDP Invoice could not start", "Open logs for details.")
    finally:
        if not keep_services_running:
            for proc in reversed(processes):
                proc.terminate()
            for proc in reversed(processes):
                try:
                    proc.wait(timeout=15)
                except subprocess.TimeoutExpired:
                    proc.kill()
        if splash is not None and not keep_services_running:
            try:
                splash.close()
            except Exception:
                pass


if __name__ == "__main__":
    main()
