"""Tests for Tally HTTP response parsing (IMPORTINFO-wrapped counters)."""
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE))

from tally.configurations.config import parse_tally_response  # noqa: E402

FLAT_RESPONSE = (
    "<RESPONSE><CREATED>1</CREATED><ALTERED>0</ALTERED>"
    "<ERRORS>0</ERRORS><EXCEPTIONS>0</EXCEPTIONS></RESPONSE>"
)

IMPORTINFO_RESPONSE = """
<RESPONSE>
  <IMPORTINFO>
    <CREATED>1</CREATED>
    <ALTERED>0</ALTERED>
    <COMBINED>0</COMBINED>
    <IGNORED>0</IGNORED>
    <ERRORS>0</ERRORS>
    <EXCEPTIONS>0</EXCEPTIONS>
  </IMPORTINFO>
</RESPONSE>
"""


def test_flat_response_created_is_success():
    r = parse_tally_response(FLAT_RESPONSE)
    assert r["created"] == 1
    assert r["success"] is True
    assert r["error_reason"] is None


def test_importinfo_wrapped_created_is_success():
    r = parse_tally_response(IMPORTINFO_RESPONSE)
    assert r["created"] == 1
    assert r["success"] is True
    assert r["error_reason"] is None


def test_altered_only_is_success():
    xml = (
        "<RESPONSE><IMPORTINFO><CREATED>0</CREATED><ALTERED>1</ALTERED>"
        "<ERRORS>0</ERRORS><EXCEPTIONS>0</EXCEPTIONS></IMPORTINFO></RESPONSE>"
    )
    r = parse_tally_response(xml)
    assert r["altered"] == 1
    assert r["success"] is True


def test_exceptions_with_created_zero_is_failure():
    xml = (
        "<RESPONSE><IMPORTINFO><CREATED>0</CREATED><ALTERED>0</ALTERED>"
        "<ERRORS>0</ERRORS><EXCEPTIONS>1</EXCEPTIONS></IMPORTINFO></RESPONSE>"
    )
    r = parse_tally_response(xml)
    assert r["success"] is False
    assert "EXCEPTIONS=1" in (r["error_reason"] or "")
