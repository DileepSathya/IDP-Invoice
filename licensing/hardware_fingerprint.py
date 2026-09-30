"""Stable Windows fingerprint based only on CPU ID and the system disk serial."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from collections.abc import Callable
from functools import lru_cache


class HardwareIdentityError(RuntimeError):
    """Windows hardware identity could not be read deterministically."""


_PLACEHOLDERS = {
    "UNKNOWN", "NONE", "NULL", "DEFAULTSTRING", "TOBEFILLEDBYO.E.M.",
    "TOBEFILLEDBYOEM", "0000000000000000", "FFFFFFFFFFFFFFFF",
}

_HARDWARE_QUERY = r"""
$ErrorActionPreference = 'Stop'
$systemDrive = [Environment]::GetEnvironmentVariable('SystemDrive')
if ([string]::IsNullOrWhiteSpace($systemDrive)) {
    $systemDrive = [System.IO.Path]::GetPathRoot(
        [Environment]::GetFolderPath([Environment+SpecialFolder]::Windows)
    )
}
$systemDrive = $systemDrive.TrimEnd('\\')
$logicalDisk = Get-CimInstance Win32_LogicalDisk -Filter "DeviceID='$systemDrive'"
if ($null -eq $logicalDisk) { throw 'Windows system drive was not found.' }
$partitions = @(Get-CimAssociatedInstance -InputObject $logicalDisk -Association Win32_LogicalDiskToPartition)
$diskDrives = @(
    $partitions |
        ForEach-Object {
            Get-CimAssociatedInstance -InputObject $_ -Association Win32_DiskDriveToDiskPartition
        } |
        Sort-Object DeviceID -Unique
)
$cpuIds = @(
    Get-CimInstance Win32_Processor |
        ForEach-Object { $_.ProcessorId } |
        Where-Object { -not [string]::IsNullOrWhiteSpace($_) } |
        Sort-Object -Unique
)
if ($cpuIds.Count -ne 1) { throw 'Expected exactly one CPU Processor ID.' }
if ($diskDrives.Count -ne 1) { throw 'Expected exactly one Windows system disk.' }
[PSCustomObject]@{
    cpuId = $cpuIds[0]
    diskSerial = $diskDrives[0].SerialNumber
} | ConvertTo-Json -Compress
"""


def normalize_hardware_id(value: str, label: str) -> str:
    normalized = re.sub(r"\s+", "", str(value or "")).upper()
    if not normalized or normalized in _PLACEHOLDERS or set(normalized) == {"0"}:
        raise HardwareIdentityError(f"{label} is unavailable or invalid.")
    return normalized


def fingerprint_from_parts(cpu_id: str, disk_serial: str) -> str:
    cpu = normalize_hardware_id(cpu_id, "CPU Processor ID")
    disk = normalize_hardware_id(disk_serial, "Windows system-disk serial number")
    canonical = f"CPU={cpu}|DISK={disk}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _run_powershell(script: str) -> str:
    creation_flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    try:
        result = subprocess.run(
            [
                "powershell.exe", "-NoLogo", "-NoProfile", "-NonInteractive",
                "-ExecutionPolicy", "Bypass", "-Command", script,
            ],
            capture_output=True,
            text=True,
            timeout=20,
            check=False,
            creationflags=creation_flags,
        )
    except Exception as exc:
        raise HardwareIdentityError("Unable to query Windows hardware identity.") from exc
    if result.returncode != 0 or not (result.stdout or "").strip():
        raise HardwareIdentityError("Unable to query Windows hardware identity.")
    return result.stdout.strip()


def _single_value(value: object, label: str) -> str:
    if isinstance(value, list):
        if len(value) != 1:
            raise HardwareIdentityError(f"Expected exactly one {label}.")
        value = value[0]
    if isinstance(value, (dict, list)) or value is None:
        raise HardwareIdentityError(f"{label} is unavailable or ambiguous.")
    return str(value)


def collect_hardware_parts(
    *,
    runner: Callable[[str], str] | None = None,
    retries: int = 2,
) -> dict[str, str]:
    query = runner or _run_powershell
    attempts = max(1, int(retries))
    last_error: Exception | None = None
    for _ in range(attempts):
        try:
            document = json.loads(query(_HARDWARE_QUERY))
            if not isinstance(document, dict):
                raise HardwareIdentityError("Windows hardware query returned invalid data.")
            cpu = normalize_hardware_id(
                _single_value(document.get("cpuId"), "CPU Processor ID"),
                "CPU Processor ID",
            )
            disk = normalize_hardware_id(
                _single_value(document.get("diskSerial"), "system-disk serial number"),
                "Windows system-disk serial number",
            )
            return {"cpu": cpu, "disk": disk}
        except Exception as exc:
            last_error = exc
    if isinstance(last_error, HardwareIdentityError):
        raise last_error
    raise HardwareIdentityError("Unable to read a stable Windows hardware identity.") from last_error


def _calculate_machine_fingerprint(runner: Callable[[str], str] | None = None) -> str:
    parts = collect_hardware_parts(runner=runner)
    return fingerprint_from_parts(parts["cpu"], parts["disk"])


@lru_cache(maxsize=1)
def _cached_machine_fingerprint() -> str:
    return _calculate_machine_fingerprint()


def clear_machine_fingerprint_cache() -> None:
    _cached_machine_fingerprint.cache_clear()


def machine_fingerprint(*, runner: Callable[[str], str] | None = None) -> str:
    if runner is not None:
        return _calculate_machine_fingerprint(runner)
    return _cached_machine_fingerprint()

