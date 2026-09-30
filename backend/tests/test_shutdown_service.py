"""Safety tests for the local IDP service shutdown helper."""

from __future__ import annotations

import json


def test_shutdown_stops_only_registered_idp_processes(tmp_path):
    from backend.shutdown_service import PID_RECORD_FILENAME, stop_registered_services

    runtime_dir = tmp_path / "data" / "runtime"
    runtime_dir.mkdir(parents=True)
    pid_file = runtime_dir / PID_RECORD_FILENAME
    api_exe = tmp_path / "idp-services" / "idp-api.exe"
    api_exe.parent.mkdir(parents=True)
    api_exe.touch()
    other_exe = tmp_path / "unrelated.exe"
    other_exe.touch()
    pid_file.write_text(
        json.dumps(
            {
                "services": [
                    {"name": "api", "pid": 101, "executable": str(api_exe)},
                    {"name": "not-idp", "pid": 202, "executable": str(other_exe)},
                ]
            }
        ),
        encoding="utf-8",
    )

    terminated: list[int] = []
    stopped = stop_registered_services(
        tmp_path,
        delay_seconds=0,
        executable_for_pid=lambda pid: str(api_exe if pid == 101 else other_exe),
        terminate_pid=terminated.append,
    )

    assert stopped == [101]
    assert terminated == [101]
    assert not pid_file.exists()


def test_shutdown_api_path_requires_dashboard_session():
    from backend.auth import requires_dashboard_auth

    assert requires_dashboard_auth("POST", "/api/system/shutdown")
