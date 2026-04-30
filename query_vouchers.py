import requests

TALLY_URL = "http://localhost:9000"

xml = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>List of Vouchers</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="List of Vouchers">
            <TYPE>Voucher</TYPE>
            <FETCH>DATE,VOUCHERTYPENAME,VOUCHERNUMBER,REFERENCE,NARRATION</FETCH>
          </COLLECTION>
        </TDLMESSAGE>
      </TDL>
    </DESC>
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

