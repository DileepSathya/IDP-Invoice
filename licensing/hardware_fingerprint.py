"""Windows machine fingerprint (shared by fingerprint_tool and license_validator)."""

from __future__ import annotations

import hashlib
import platform
import subprocess
import uuid


def _run_wmic(args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["wmic", *args],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
        )
        lines = [ln.strip() for ln in (result.stdout or "").splitlines() if ln.strip()]
        if len(lines) >= 2:
            return lines[1]
    except Exception:
        pass
    return ""


def collect_hardware_parts() -> dict[str, str]:
    mac = f"{uuid.getnode():012x}"
    cpu_name = (platform.processor() or "").strip()
    cpu_id = _run_wmic(["cpu", "get", "ProcessorId"])
    disk_serial = _run_wmic(["diskdrive", "get", "serialnumber"])
    return {
        "mac": mac,
        "cpu": f"{cpu_name}|{cpu_id}".strip("|"),
        "disk": disk_serial,
    }


def machine_fingerprint() -> str:
    parts = collect_hardware_parts()
    blob = f"{parts['mac']}:{parts['cpu']}:{parts['disk']}"
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()
