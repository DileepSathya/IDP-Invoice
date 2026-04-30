import requests

TALLY_URL = "http://localhost:9000"

xml = """<ENVELOPE>
  <HEADER>
    <VERSION>1</VERSION>
    <TALLYREQUEST>Export</TALLYREQUEST>
    <TYPE>Collection</TYPE>
    <ID>List of Ledgers</ID>
  </HEADER>
  <BODY>
    <DESC>
      <STATICVARIABLES>
        <SVEXPORTFORMAT>$$SysName:XML</SVEXPORTFORMAT>
      </STATICVARIABLES>
    </DESC>
  </BODY>
</ENVELOPE>"""

r = requests.post(
    TALLY_URL,
    data=xml.encode("utf-8"),
    headers={"Content-Type": "text/xml; charset=utf-8"},
    timeout=10,
)
print(r.status_code)
print(r.text[:5000])

