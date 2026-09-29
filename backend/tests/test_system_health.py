"""System health severity: errors vs warnings for Home vs Health page."""

from __future__ import annotations

from unittest.mock import patch

from backend.system_health import _check_pipeline_section, _critical_messages, _item


def test_critical_messages_only_core_errors():
    sections = [
        {
            "items": [
                _item(
                    item_id="mongodb",
                    label="MongoDB",
                    status="error",
                    message="down",
                ),
                _item(
                    item_id="pipeline_error_folder",
                    label="ERROR folder",
                    status="warning",
                    message="3 file(s)",
                ),
                _item(
                    item_id="hitl_email",
                    label="HITL email (SMTP)",
                    status="warning",
                    message="Missing SMTP",
                ),
            ]
        }
    ]
    messages = _critical_messages(sections)
    assert len(messages) == 1
    assert messages[0].startswith("MongoDB:")


def test_pipeline_section_uses_warnings_not_errors():
    section = _check_pipeline_section(
        {
            "error": 2,
            "gemini_api_error": 1,
            "gemini_quota_error_count": 5,
            "network_error_count": 1,
        }
    )
    assert section["status"] == "warning"
    for item in section["items"]:
        assert item["status"] == "warning"


def test_home_overall_warning_when_only_pipeline_issues():
    from backend.system_health import _compute_overall

    sections = [_check_pipeline_section({"error": 1})]
    overall = _compute_overall(sections)
    assert overall == "warning"
