import requests

TALLY_URL = "http://localhost:9000"

xml = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Data</TYPE>
    <ID>Day Book</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
        <SVFROMDATE>20260401</SVFROMDATE>
        <SVTODATE>20260401</SVTODATE>
      </STATICVARIABLES>
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

