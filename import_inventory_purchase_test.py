import requests

TALLY_URL = "http://localhost:9000"
TALLY_COMPANY = "Python Automated Company Ltd_1"

STOCK_ITEM = "Waltr A Monthly Subscription Water Level &Usage Monitor"
UOM = "Nos"

def esc(x: str) -> str:
    return (
        str(x)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )

xml = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>{TALLY_COMPANY}</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Purchase" ACTION="Create" OBJVIEW="Accounting Voucher View">
            <PERSISTEDVIEW>Accounting Voucher View</PERSISTEDVIEW>
            <DATE>20260401</DATE>
            <EFFECTIVEDATE>20260401</EFFECTIVEDATE>
            <VCHSTATUSDATE>20260401</VCHSTATUSDATE>
            <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
            <PARTYLEDGERNAME>Kre38 Labs Pvt Ltd</PARTYLEDGERNAME>
            <REFERENCE>TEST-INV-001</REFERENCE>
            <NARRATION>Inventory purchase import test</NARRATION>
            <ISINVOICE>No</ISINVOICE>

            <ALLINVENTORYENTRIES.LIST>
              <STOCKITEMNAME>{esc(STOCK_ITEM)}</STOCKITEMNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <RATE>1.00/{UOM}</RATE>
              <AMOUNT>-100.00</AMOUNT>
              <ACTUALQTY>1 {UOM}</ACTUALQTY>
              <BILLEDQTY>1 {UOM}</BILLEDQTY>
              <ACCOUNTINGALLOCATIONS.LIST>
                <LEDGERNAME>GST Purchase</LEDGERNAME>
                <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
                <AMOUNT>-100.00</AMOUNT>
              </ACCOUNTINGALLOCATIONS.LIST>
            </ALLINVENTORYENTRIES.LIST>

            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>Kre38 Labs Pvt Ltd</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
              <AMOUNT>100.00</AMOUNT>
              <BILLALLOCATIONS.LIST>
                <NAME>TEST-INV-001</NAME>
                <BILLTYPE>New Ref</BILLTYPE>
                <AMOUNT>100.00</AMOUNT>
              </BILLALLOCATIONS.LIST>
            </ALLLEDGERENTRIES.LIST>
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>"""

r = requests.post(
    TALLY_URL,
    data=xml.encode("utf-8"),
    headers={"Content-Type": "text/xml; charset=utf-8"},
    timeout=30,
)
print(r.status_code)
print(r.text)
print("RAW_BYTES:", r.content)

