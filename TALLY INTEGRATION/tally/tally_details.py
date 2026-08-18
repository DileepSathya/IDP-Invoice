"""
Here we will return the comapany name list of ledgers and stock items
"""

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Optional

import requests

from tally.configurations.config import clean_tally_xml, normalize_to_bytes

logger = logging.getLogger(__name__)

PURCHASE_ACCOUNTS_GROUP = "Purchase Accounts"


def _xml_scripts_dir() -> Path:
    """Resolve Tally XML templates for dev, portable external copy, and PyInstaller bundle."""
    env = os.environ.get("TALLY_XML_SCRIPTS_DIR", "").strip()
    if env:
        return Path(env)
    cwd_scripts = Path.cwd() / "xml_scripts"
    if cwd_scripts.is_dir():
        return cwd_scripts
    return Path(__file__).resolve().parent.parent / "xml_scripts"




def active_company(TALLY_URL,xml_request):
    """
    xml_request: either an ET.Element, ET.ElementTree, or an XML string/bytes.
    """


    headers = {"Content-Type": "text/xml;charset=utf-8"}
    response = requests.post(TALLY_URL, data=normalize_to_bytes(xml_request), headers=headers)
    print(response)
   

    if response.status_code == 200:
        root = ET.fromstring(response.content)
        
        company_tag = root.find(".//COMPANY/NAME")


        if company_tag is not None and company_tag.text:
            company_name=company_tag.text.strip()
            
        else:
            print("No active company found. Make sure a company is opened in Tally.")
    else:
        print(f"Failed to connect. HTTP Status Code: {response.status_code}")
    return company_name


def ledgers_retriver(TALLY_URL,xml_request):
    ledger_dict = {}
    try:
        headers = {"Content-Type": "text/xml;charset=utf-8"}
        response = requests.post(TALLY_URL, data=normalize_to_bytes(xml_request), headers=headers)

        if response.status_code == 200:
            raw_text = response.content.decode("utf-8", errors="replace")
            cleaned_text = clean_tally_xml(raw_text)
            root = ET.fromstring(cleaned_text)

            ledgers = root.findall(".//LEDGER")

            if ledgers:
                for ledger in ledgers:
                    name = ledger.get("NAME", "Unknown").strip()

                    parent_tag = ledger.find("PARENT")
                    parent = parent_tag.text.strip() if parent_tag is not None and parent_tag.text else "None"

                    # Group ledger names under their parent as a list
                    ledger_dict.setdefault(parent, []).append(name)

            else:
                print("No ledgers found. Ensure a company is currently open in Tally.")

        else:
            print(f"Failed to connect. HTTP Status Code: {response.status_code}")

    except requests.exceptions.ConnectionError:
        print("Error: Cannot connect to Tally. Verify Tally is running on Port 9000.")
    except Exception as e:
        print(f"An error occurred: {e}")

    return ledger_dict


def _load_xml_template(filename: str, *, company_name: Optional[str] = None) -> str:
    path = _xml_scripts_dir() / filename
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


def _parse_ledger_names(response_text: str) -> list[str]:
    cleaned_text = clean_tally_xml(response_text)
    root = ET.fromstring(cleaned_text)
    names: list[str] = []
    for ledger in root.findall(".//LEDGER"):
        name = (ledger.get("NAME") or "").strip()
        if not name:
            name_tag = ledger.find("NAME")
            if name_tag is not None and name_tag.text:
                name = name_tag.text.strip()
        if name and name not in names:
            names.append(name)
    return names


def get_purchase_ledgers(
    tally_url: str,
    *,
    company_name: Optional[str] = None,
) -> tuple[list[str], Optional[str]]:
    """Return purchase-account ledger names from Tally (includes nested sub-groups)."""
    errors: list[str] = []

    for template in ("purchase_ledger_list.xml", "ledger_list.xml"):
        try:
            xml_request = _load_xml_template(template, company_name=company_name)
            headers = {"Content-Type": "text/xml;charset=utf-8"}
            response = requests.post(
                tally_url,
                data=normalize_to_bytes(xml_request),
                headers=headers,
                timeout=30,
            )
            if response.status_code != 200:
                errors.append(f"{template}: HTTP {response.status_code}")
                continue

            raw_text = response.content.decode("utf-8", errors="replace")
            if template == "purchase_ledger_list.xml":
                names = _parse_ledger_names(raw_text)
            else:
                cleaned_text = clean_tally_xml(raw_text)
                root = ET.fromstring(cleaned_text)
                names = []
                for ledger in root.findall(".//LEDGER"):
                    parent_tag = ledger.find("PARENT")
                    parent = (
                        parent_tag.text.strip()
                        if parent_tag is not None and parent_tag.text
                        else ""
                    )
                    if parent != PURCHASE_ACCOUNTS_GROUP:
                        continue
                    name = (ledger.get("NAME") or "").strip()
                    if not name:
                        name_tag = ledger.find("NAME")
                        if name_tag is not None and name_tag.text:
                            name = name_tag.text.strip()
                    if name:
                        names.append(name)

            names = sorted({n.strip() for n in names if n and n.strip()})
            if names:
                return names, None
            errors.append(f"{template}: no purchase ledgers found")
        except requests.exceptions.ConnectionError:
            return [], "Cannot connect to Tally. Verify Tally is running on port 9000."
        except Exception as exc:
            errors.append(f"{template}: {exc}")

    detail = "; ".join(errors) if errors else "No purchase ledgers found in Tally"
    return [], detail


def stock_items(TALLY_URL,xml_request):
    stock_fin_list=[]
    headers = {"Content-Type": "text/xml;charset=utf-8"}
    response = requests.post(TALLY_URL, data=normalize_to_bytes(xml_request), headers=headers)

    if response.status_code == 200:
            # Decode and clean before parsing
        raw_text = response.content.decode("utf-8", errors="replace")

        cleaned_text = clean_tally_xml(raw_text)

        root = ET.fromstring(cleaned_text)

        stock_list = root.findall(".//STOCKITEM")
        if stock_list:

            for item in stock_list:
                name=item.get("NAME", "Unknown").strip()
                parent_tag = item.find("PARENT")
                parent = parent_tag.text.strip() if parent_tag is not None and parent_tag.text else "None"
                unit_tag = item.find("BASEUNITS")
                unit = unit_tag.text.strip() if unit_tag is not None and unit_tag.text else "N/A"

                stock_fin_list.append(name)
            
        else:
            print("No stock items found in tally")

    else:
        print(f"Failed to connect. HTTP Status Code: {response.status_code}")
    return stock_fin_list
