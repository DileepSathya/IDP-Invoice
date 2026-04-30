import requests

TALLY_URL = "http://localhost:9000"
TALLY_COMPANY = "Python Automated Company Ltd_1"

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
            <DATE>20260401</DATE>
            <VOUCHERTYPENAME>Purchase</VOUCHERTYPENAME>
            <PARTYLEDGERNAME>Kre38 Labs Pvt Ltd</PARTYLEDGERNAME>
            <REFERENCE>TEST-001</REFERENCE>
            <NARRATION>Simple purchase import test</NARRATION>

            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>Kre38 Labs Pvt Ltd</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <ISPARTYLEDGER>Yes</ISPARTYLEDGER>
              <AMOUNT>100.00</AMOUNT>
              <BILLALLOCATIONS.LIST>
                <NAME>TEST-001</NAME>
                <BILLTYPE>New Ref</BILLTYPE>
                <AMOUNT>100.00</AMOUNT>
              </BILLALLOCATIONS.LIST>
            </ALLLEDGERENTRIES.LIST>

            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>GST Purchase</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <ISPARTYLEDGER>No</ISPARTYLEDGER>
              <AMOUNT>-100.00</AMOUNT>
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

