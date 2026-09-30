"""Stop only the IDP processes recorded by the portable launcher."""

from __future__ import annotations

import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable


PID_RECORD_FILENAME = "service-pids.json"
_RUNTIME_PATH = Path("data") / "runtime" / PID_RECORD_FILENAME
_SERVICE_EXECUTABLES = {
    "mongo": Path("mongodb") / "bin" / "mongod.exe",
    "watcher": Path("idp-services") / "idp-watcher.exe",
    "tally_bridge": Path("tally-bridge") / "tally-bridge.exe",
    "api": Path("idp-services") / "idp-api.exe",
}


def pid_record_path(root: Path) -> Path:
    return root / _RUNTIME_PATH


def _process_executable(pid: int) -> str | None:
    if os.name != "nt":
        return None
    process = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
    if not process:
        return None
    try:
        size = ctypes.c_ulong(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not ctypes.windll.kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            return None
        return buffer.value
    finally:
        ctypes.windll.kernel32.CloseHandle(process)


def _terminate_process(pid: int) -> None:
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(pid), "/T", "/F"], check=False,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW,
        )
    else:
        os.kill(pid, signal.SIGTERM)


def stop_registered_services(
    root: Path,
    *,
    delay_seconds: float = 0.75,
    executable_for_pid: Callable[[int], str | None] = _process_executable,
    terminate_pid: Callable[[int], None] = _terminate_process,
) -> list[int]:
    """Remove the launch record and terminate only verified IDP service PIDs."""
    record_path = pid_record_path(root)
    try:
        payload = json.loads(record_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    try:
        record_path.unlink()
    except OSError:
        pass
    if delay_seconds:
        time.sleep(delay_seconds)

    stopped: list[int] = []
    for record in payload.get("services", []):
        if not isinstance(record, dict):
            continue
        name = record.get("name")
        pid = record.get("pid")
        expected_relative = _SERVICE_EXECUTABLES.get(name)
        if not expected_relative or not isinstance(pid, int) or pid <= 0:
            continue
        expected = (root / expected_relative).resolve()
        if str(expected) != record.get("executable"):
            continue
        actual = executable_for_pid(pid)
        if not actual or Path(actual).resolve() != expected:
            continue
        terminate_pid(pid)
        stopped.append(pid)
    return stopped


def start_shutdown_helper() -> None:
    """Run a second API executable after the HTTP response has been sent."""
    flags = 0
    if os.name == "nt":
        flags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
    subprocess.Popen(
        [sys.executable, "--shutdown-services"],
        cwd=str(Path(sys.executable).resolve().parent),
        creationflags=flags,
        close_fds=True,
    )
