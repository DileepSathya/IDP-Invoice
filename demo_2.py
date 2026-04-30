import requests

# Tally Server URL (Default port is 9000)
url = "http://localhost:9000"

# XML Request to Create a Company
# Note: Unlike Ledgers, Tally requires specific tags for company creation.
# Tally's 'IMPORTDATA' for 'All Masters' is the standard way to send such XML.
xml_data = """
<ENVELOPE>
    <HEADER>
        <TALLYREQUEST>Import Data</TALLYREQUEST>
    </HEADER>
    <BODY>
        <IMPORTDATA>
            <REQUESTDESC>
                <REPORTNAME>All Masters</REPORTNAME>
            </REQUESTDESC>
            <REQUESTDATA>
                <TALLYMESSAGE xmlns:UDF="TallyUDF">
                    <COMPANY Action="Create">
                        <NAME>Python Automated Company Ltd_1</NAME>
                        <BOOKSBEGINNINGFROM>20240401</BOOKSBEGINNINGFROM>
                        <FIRSTVOUCHERDATE>20240401</FIRSTVOUCHERDATE>
                        <BASICCURRENCYNAME>INR</BASICCURRENCYNAME>
                        <BASICCURRENCYSYMBOL>₹</BASICCURRENCYSYMBOL>
                        <COUNTRY>India</COUNTRY>
                        <STATENAME>Maharashtra</STATENAME>
                        <GUID>Python-Auto-ID-004</GUID>
                    </COMPANY>
                </TALLYMESSAGE>
            </REQUESTDATA>
        </IMPORTDATA>
    </BODY>
</ENVELOPE>
"""

try:
    response = requests.post(url, data=xml_data, headers={'Content-Type': 'text/xml'})
    if response.status_code == 200:
        print("Response from Tally:")
        print(response.text)
    else:
        print(f"Failed to connect. Status Code: {response.status_code}")
except Exception as e:
    print(f"An error occurred: {e}")
