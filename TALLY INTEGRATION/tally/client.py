"""Shared HTTP + XML-template plumbing for talking to Tally, used by every
tally.* fetch module (masters.py, expenses.py, and so on) so the request and
response mechanics live in exactly one place instead of being copy-pasted
per module.

Place this file at tally/_client.py, alongside tally/masters.py - the
directory-relative default in xml_scripts_dir() below assumes that.
"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Optional

import requests

from tally.configurations.config import normalize_to_bytes


def xml_scripts_dir() -> Path:
    env = os.environ.get("TALLY_XML_SCRIPTS_DIR", "").strip()
    if env:
        return Path(env)
    cwd_scripts = Path.cwd() / "xml_scripts"
    if cwd_scripts.is_dir():
        return cwd_scripts
    return Path(__file__).resolve().parent.parent / "xml_scripts"


def load_xml_template(filename: str, *, company_name: Optional[str] = None) -> str:
    path = xml_scripts_dir() / filename
    if not path.is_file():
        raise FileNotFoundError(f"Tally XML template not found: {path}")
    xml_text = path.read_text(encoding="utf-8")
    if company_name:
        company_tag = f"<SVCURRENTCOMPANY>{company_name}</SVCURRENTCOMPANY>"
        if "<SVCURRENTCOMPANY>" not in xml_text:
            xml_text = xml_text.replace(
                "<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>",
                f"<SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>\n                {company_tag}",
            )
        else:
            xml_text = re.sub(
                r"<SVCURRENTCOMPANY>.*?</SVCURRENTCOMPANY>",
                company_tag,
                xml_text,
                count=1,
            )
    return xml_text


def post_tally(tally_url: str, xml_request: str, *, timeout: int = 60) -> tuple[str, Optional[str]]:
    headers = {"Content-Type": "text/xml;charset=utf-8"}
    try:
        response = requests.post(
            tally_url,
            data=normalize_to_bytes(xml_request),
            headers=headers,
            timeout=timeout,
        )
    except requests.exceptions.ConnectionError:
        return "", "Cannot connect to Tally. Verify Tally is running on port 9000."
    except requests.exceptions.Timeout:
        return "", "Tally request timed out."

    if response.status_code != 200:
        return "", f"Tally returned HTTP {response.status_code}"

    return response.content.decode("utf-8", errors="replace"), None