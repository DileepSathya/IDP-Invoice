import json
import requests
from datetime import datetime

TALLY_URL = "http://localhost:9000"
TALLY_COMPANY = "Python Automated Company Ltd_1"  # loaded company name in TallyPrime

# These names will be auto-created (basic masters) if missing.
PURCHASE_LEDGER = "GST Purchase"
IGST_LEDGER = "IGST"
ROUND_OFF_LEDGER = "Round Off"
DISCOUNT_LEDGER = "Discount"

UOM = "Nos"
GODOWN = "Main Location"
BATCH = "Primary Batch"
file_name = "invoice_2.json"
def to_yyyymmdd(date_str: str) -> str:
    # Your JSON is "01/12/2025" (assumed DD/MM/YYYY)
    return datetime.strptime(date_str, "%d/%m/%Y").strftime("%Y%m%d")

def esc(x: str) -> str:
    return (
        str(x)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )

def post_to_tally(xml: str, timeout_s: int = 120) -> str:
    resp = requests.post(
        TALLY_URL,
        data=xml.encode("utf-8"),
        headers={"Content-Type": "text/xml; charset=utf-8"},
        timeout=timeout_s,
    )
    print(resp.status_code)
    print(resp.text)
    return resp.text

def import_master_xml(tallymessage_xml: str) -> str:
    xml = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>All Masters</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>{esc(TALLY_COMPANY)}</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          {tallymessage_xml}
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""
    return post_to_tally(xml)

def ensure_basic_masters(vendor: str, stock_items: list[str]) -> None:
    # 1) Unit of Measure
    import_master_xml(f"""
      <UNIT NAME="{esc(UOM)}" ACTION="Create">
        <NAME>{esc(UOM)}</NAME>
        <ISSIMPLEUNIT>Yes</ISSIMPLEUNIT>
        <ORIGINALNAME>Numbers</ORIGINALNAME>
        <DECIMALPLACES>0</DECIMALPLACES>
      </UNIT>
    """)

    # 1b) Root stock group (some new companies may not have inventory groups yet)
    import_master_xml(f"""
      <STOCKGROUP NAME="Primary" ACTION="Create">
        <NAME>Primary</NAME>
      </STOCKGROUP>
    """)

    # 2) Vendor ledger (party)
    import_master_xml(f"""
      <LEDGER ACTION="Create">
        <NAME>{esc(vendor)}</NAME>
        <PARENT>Sundry Creditors</PARENT>
      </LEDGER>
    """)

    # 3) Purchase ledger and tax/adjustment ledgers
    import_master_xml(f"""
      <LEDGER ACTION="Create">
        <NAME>{esc(PURCHASE_LEDGER)}</NAME>
        <PARENT>Purchase Accounts</PARENT>
      </LEDGER>
      <LEDGER ACTION="Create">
        <NAME>{esc(IGST_LEDGER)}</NAME>
        <PARENT>Duties &amp; Taxes</PARENT>
      </LEDGER>
      <LEDGER ACTION="Create">
        <NAME>{esc(ROUND_OFF_LEDGER)}</NAME>
        <PARENT>Indirect Expenses</PARENT>
      </LEDGER>
      <LEDGER ACTION="Create">
        <NAME>{esc(DISCOUNT_LEDGER)}</NAME>
        <PARENT>Indirect Expenses</PARENT>
      </LEDGER>
    """)

    # 4) Stock items (minimal)
    for si in stock_items:
        import_master_xml(f"""
          <STOCKITEM NAME="{esc(si)}" ACTION="Create">
            <NAME>{esc(si)}</NAME>
            <PARENT>Primary</PARENT>
            <BASEUNITS>{esc(UOM)}</BASEUNITS>
          </STOCKITEM>
        """)

def ensure_vendor_ledger(vendor: str) -> None:
    # Minimal master ensure for accounting-view purchase import.
    import_master_xml(f"""
      <LEDGER ACTION="Create">
        <NAME>{esc(vendor)}</NAME>
        <PARENT>Sundry Creditors</PARENT>
      </LEDGER>
    """)

with open(file_name, "r", encoding="utf-8") as f:
    doc = json.load(f)

inv = doc["gemini"]["json"]

invoice_no = inv["invoice_number"]
# TallyPrime rejects vouchers outside the currently selected period.
# This company is set to FY 2026-27, while this invoice is dated 01/12/2025.
# Use the ingestion/created_at date (within current period) to allow upload,
# and keep original invoice date in narration/reference for audit.
created_at_iso = (doc.get("created_at") or {}).get("$date")
created_at_yyyymmdd = None
if created_at_iso:
    # Example: "2026-04-30T07:35:25.675Z"
    created_at_yyyymmdd = created_at_iso[:10].replace("-", "")
date_yyyymmdd = created_at_yyyymmdd or to_yyyymmdd(inv["invoice_date"])
fallback_date_yyyymmdd = created_at_yyyymmdd or "20260401"
if fallback_date_yyyymmdd == date_yyyymmdd:
    fallback_date_yyyymmdd = "20260401"
vendor = inv["seller"]
total = float(inv["total_amount"])

# Your extracted structure has one line item; this supports many
items = inv.get("line_items", [])

# Amount convention used in many Tally XML imports for Purchase Invoice (inventory view):
# - inventory line AMOUNT negative
# - purchase ledger allocation negative
# - tax ledger negative
# - party ledger positive (total payable)
inventory_xml = []
tax_total = 0.0
taxable_total = 0.0

for it in items:
    stock_item = it["service"]  # you can map this to an existing stock item name
    qty = float(it["quantity"])
    rate = float(it["price_per_unit"])
    taxable = float(it["amount"])
    tax = float(it["tax_amount"])
    tax_total += tax
    taxable_total += taxable

    # Keep line item text for narration; this run uses Accounting Voucher View (no inventory entries)
    inventory_xml.append(f"{stock_item} x{qty:g} @ {rate:.2f} => {taxable:.2f}")

# Ensure only the vendor ledger exists for this invoice.
ensure_vendor_ledger(vendor=vendor)

# Optional: discount / round off from your JSON
discount = float(inv.get("additional_fields", {}).get("discount_amount", 0) or 0)
round_off = float(inv.get("additional_fields", {}).get("round_off_amount", 0) or 0)

# If your taxable already reflects discount (your sample taxable=9653 already net of discount),
# then DO NOT post discount again as a ledger, or totals will mismatch.
# Here we assume taxable is already net, so we don't apply discount ledger by default.
discount_ledger_xml = ""  # keep empty unless you intentionally separate discount

roundoff_ledger_xml = ""
if abs(round_off) > 0:
    # Round off in your OCR is +0.46 (increasing total). For purchase vouchers,
    # sign might need flipping depending on how you model it; adjust if mismatch.
    roundoff_ledger_xml = f"""
      <ALLLEDGERENTRIES.LIST>
        <LEDGERNAME>{esc(ROUND_OFF_LEDGER)}</LEDGERNAME>
        <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
        <ISPARTYLEDGER>No</ISPARTYLEDGER>
        <AMOUNT>{-round_off:.2f}</AMOUNT>
      </ALLLEDGERENTRIES.LIST>
    """

def build_voucher_xml(voucher_date: str) -> str:
    return f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>{esc(TALLY_COMPANY)}</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Purchase" ACTION="Create" OBJVIEW="Accounting Voucher View">
            <PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
            <DATE>{voucher_date}</DATE>
            <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
            <PARTYLEDGERNAME>{esc(vendor)}</PARTYLEDGERNAME>
            <REFERENCE>{esc(invoice_no)}</REFERENCE>
            <NARRATION>Imported from JSON. SourceInvoiceDate={esc(inv["invoice_date"])}; Items: {esc(' | '.join(inventory_xml))}</NARRATION>
            <ISINVOICE>No</ISINVOICE>

            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{esc(vendor)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
              <AMOUNT>{total:.2f}</AMOUNT>
              <BILLALLOCATIONS.LIST>
                <NAME>{esc(invoice_no)}</NAME>
                <BILLTYPE>New Ref</BILLTYPE>
                <AMOUNT>{total:.2f}</AMOUNT>
              </BILLALLOCATIONS.LIST>
            </ALLLEDGERENTRIES.LIST>

            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{esc(PURCHASE_LEDGER)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <ISPARTYLEDGER>No</ISPARTYLEDGER>
              <AMOUNT>{-taxable_total:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>

            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{esc(IGST_LEDGER)}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <ISPARTYLEDGER>No</ISPARTYLEDGER>
              <AMOUNT>{-tax_total:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>

            {discount_ledger_xml}
            {roundoff_ledger_xml}

          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>
"""

print("Trying invoice DATE:", date_yyyymmdd, "for invoice", invoice_no)
response_text = post_to_tally(build_voucher_xml(date_yyyymmdd))

# If invoice date is not accepted by current Tally period/rules, retry with fallback date.
if ("Out of Range" in response_text) or ("Voucher date is missing" in response_text):
    print(
        "Invoice date not accepted by Tally. Retrying with fallback DATE:",
        fallback_date_yyyymmdd,
    )
    response_text = post_to_tally(build_voucher_xml(fallback_date_yyyymmdd))