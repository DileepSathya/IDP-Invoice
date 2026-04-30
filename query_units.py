import requests

TALLY_URL = "http://localhost:9000"
TALLY_COMPANY = "Python Automated Company Ltd_1"

xml = f"""<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>List of Units</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        <SVCURRENTCOMPANY>{TALLY_COMPANY}</SVCURRENTCOMPANY>
      </STATICVARIABLES>
      <TDL>
        <TDLMESSAGE>
          <COLLECTION NAME="List of Units">
            <TYPE>Unit</TYPE>
            <FETCH>Name</FETCH>
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

