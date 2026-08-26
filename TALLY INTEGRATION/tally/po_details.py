import requests
import xml.etree.ElementTree as ET
import re


# ============================================================
# CONFIGURATION
# ============================================================

TALLY_URL = "http://localhost:9000"

COMPANY_NAME = "My Home Jewel Apartments"


# ============================================================
# CLEAN TALLY XML
# ============================================================

def clean_tally_xml(xml_text):
    """
    Tally can sometimes return invalid XML character references
    such as &#4;. XML 1.0 does not allow these characters.

    This function removes invalid numeric character references
    before parsing the XML.
    """

    def replace_numeric_reference(match):

        reference = match.group(0)

        try:

            # Hexadecimal reference
            if reference.lower().startswith("&#x"):
                number = int(
                    reference[3:-1],
                    16
                )

            # Decimal reference
            else:
                number = int(
                    reference[2:-1]
                )

            # Valid XML 1.0 character ranges
            valid = (
                number == 0x9
                or number == 0xA
                or number == 0xD
                or 0x20 <= number <= 0xD7FF
                or 0xE000 <= number <= 0xFFFD
                or 0x10000 <= number <= 0x10FFFF
            )

            if not valid:

                print(
                    f"Removing invalid XML reference: "
                    f"{reference} "
                    f"(U+{number:04X})"
                )

                return ""

            return reference

        except ValueError:

            return reference


    # Find numeric XML references:
    #
    # &#4;
    # &#123;
    # &#x04;
    # &#x1A;
    #

    xml_text = re.sub(
        r"&#(?:[0-9]+|[xX][0-9a-fA-F]+);",
        replace_numeric_reference,
        xml_text
    )


    # Also remove actual invalid control characters

    result = []

    for char in xml_text:

        code = ord(char)

        if (
            code == 0x9
            or code == 0xA
            or code == 0xD
            or 0x20 <= code <= 0xD7FF
            or 0xE000 <= code <= 0xFFFD
            or 0x10000 <= code <= 0x10FFFF
        ):

            result.append(char)

    return "".join(result)


# ============================================================
# HELPER FUNCTIONS
# ============================================================

def get_text(element, tag):

    """
    Safely get text from an XML element.
    """

    child = element.find(tag)

    if child is not None and child.text:

        return child.text.strip()

    return ""


def get_text_anywhere(element, tag):

    """
    Search recursively for a tag.
    Useful for fields such as ORDERNO that may not
    exist directly under VOUCHER.
    """

    child = element.find(
        f".//{tag}"
    )

    if child is not None and child.text:

        return child.text.strip()

    return ""


# ============================================================
# TALLY XML REQUEST
# ============================================================

xml_payload = f"""<?xml version="1.0" encoding="UTF-8"?>

<ENVELOPE>

    <HEADER>

        <VERSION>1</VERSION>

        <TALLYREQUEST>EXPORT</TALLYREQUEST>

        <TYPE>DATA</TYPE>

        <ID>Voucher Register</ID>

    </HEADER>


    <BODY>

        <DESC>

            <STATICVARIABLES>

                <SVCURRENTCOMPANY>
                    {COMPANY_NAME}
                </SVCURRENTCOMPANY>

                <SVEXPORTFORMAT>
                    $$SysName:XML
                </SVEXPORTFORMAT>

                <VoucherTypeName>
                    Purchase Order
                </VoucherTypeName>

            </STATICVARIABLES>

        </DESC>

    </BODY>

</ENVELOPE>
"""


# ============================================================
# CONNECT TO TALLY
# ============================================================

try:

    print()
    print("=" * 70)
    print("TALLY PURCHASE ORDER FETCH")
    print("=" * 70)

    print(
        f"Company: {COMPANY_NAME}"
    )

    print()


    # --------------------------------------------------------
    # SEND REQUEST
    # --------------------------------------------------------

    response = requests.post(

        TALLY_URL,

        data=xml_payload.encode(
            "utf-8"
        ),

        headers={
            "Content-Type":
                "text/xml; charset=utf-8"
        },

        timeout=60
    )


    print(
        "HTTP Status:",
        response.status_code
    )


    # --------------------------------------------------------
    # HTTP ERROR
    # --------------------------------------------------------

    if response.status_code != 200:

        print()
        print(
            "Tally returned an HTTP error:"
        )

        print(
            response.text
        )

        exit()


    # --------------------------------------------------------
    # DECODE RESPONSE
    # --------------------------------------------------------

    response_text = response.content.decode(

        "utf-8",

        errors="replace"

    )


    # --------------------------------------------------------
    # CLEAN XML
    # --------------------------------------------------------

    print()
    print(
        "Cleaning Tally XML..."
    )

    cleaned_xml = clean_tally_xml(
        response_text
    )


    # --------------------------------------------------------
    # SAVE RESPONSE
    # --------------------------------------------------------

    with open(

        "tally_response_cleaned.xml",

        "w",

        encoding="utf-8"

    ) as file:

        file.write(
            cleaned_xml
        )


    print(
        "Cleaned XML saved to "
        "tally_response_cleaned.xml"
    )


    # --------------------------------------------------------
    # PARSE XML
    # --------------------------------------------------------

    print()
    print(
        "Parsing XML..."
    )

    root = ET.fromstring(
        cleaned_xml
    )

    print(
        "XML parsed successfully."
    )


    # ========================================================
    # FIND PURCHASE ORDER VOUCHERS
    # ========================================================

    vouchers = root.findall(
        ".//VOUCHER"
    )


    print()
    print("=" * 70)

    print(
        f"Purchase Orders found: "
        f"{len(vouchers)}"
    )

    print("=" * 70)


    if not vouchers:

        print()
        print(
            "No Purchase Orders found."
        )

        exit()


    # ========================================================
    # PROCESS EACH PURCHASE ORDER
    # ========================================================

    for index, voucher in enumerate(

        vouchers,

        start=1

    ):


        # ----------------------------------------------------
        # TALLY INTERNAL ID
        # ----------------------------------------------------

        master_id = get_text(
            voucher,
            "MASTERID"
        )


        # ----------------------------------------------------
        # TALLY VOUCHER NUMBER
        # ----------------------------------------------------

        voucher_number = get_text(
            voucher,
            "VOUCHERNUMBER"
        )


        # ----------------------------------------------------
        # ACTUAL PURCHASE ORDER NUMBER
        # ----------------------------------------------------
        #
        # Example:
        #
        # Tally Voucher No = 9
        # Order No         = PO1001
        #
        # We want PO1001.
        #

        order_no = get_text_anywhere(
            voucher,
            "ORDERNO"
        )


        # ----------------------------------------------------
        # DATE
        # ----------------------------------------------------

        voucher_date = get_text(
            voucher,
            "DATE"
        )


        # ----------------------------------------------------
        # PARTY
        # ----------------------------------------------------

        party_name = get_text(
            voucher,
            "PARTYLEDGERNAME"
        )


        # ----------------------------------------------------
        # VOUCHER TYPE
        # ----------------------------------------------------

        voucher_type = get_text(
            voucher,
            "VOUCHERTYPENAME"
        )


        # ====================================================
        # DISPLAY PURCHASE ORDER
        # ====================================================

        print()
        print("=" * 70)

        print(
            f"PURCHASE ORDER #{index}"
        )

        print("=" * 70)


        print(
            f"Master ID     : "
            f"{master_id}"
        )


        print(
            f"Voucher No    : "
            f"{voucher_number}"
        )


        print(
            f"Order No      : "
            f"{order_no}"
        )


        print(
            f"Date          : "
            f"{voucher_date}"
        )


        print(
            f"Party         : "
            f"{party_name}"
        )


        print(
            f"Voucher Type  : "
            f"{voucher_type}"
        )


        # ====================================================
        # INVENTORY ITEMS
        # ====================================================

        inventory_entries = voucher.findall(
            ".//ALLINVENTORYENTRIES.LIST"
        )


        print()
        print(
            f"Items found: "
            f"{len(inventory_entries)}"
        )


        if not inventory_entries:

            print(
                "No inventory entries found."
            )


        # ----------------------------------------------------
        # PROCESS ITEMS
        # ----------------------------------------------------

        for item_index, item in enumerate(

            inventory_entries,

            start=1

        ):


            stock_item = get_text(
                item,
                "STOCKITEMNAME"
            )


            quantity = get_text(
                item,
                "ACTUALQTY"
            )


            billed_quantity = get_text(
                item,
                "BILLEDQTY"
            )


            rate = get_text(
                item,
                "RATE"
            )


            amount = get_text(
                item,
                "AMOUNT"
            )


            # ------------------------------------------------
            # DISPLAY ITEM
            # ------------------------------------------------

            print()
            print(
                f"Item #{item_index}"
            )

            print(
                "-" * 50
            )


            print(
                f"Stock Item      : "
                f"{stock_item}"
            )


            print(
                f"Quantity        : "
                f"{quantity}"
            )


            print(
                f"Billed Quantity : "
                f"{billed_quantity}"
            )


            print(
                f"Rate            : "
                f"{rate}"
            )


            print(
                f"Amount          : "
                f"{amount}"
            )


    # ========================================================
    # FINISHED
    # ========================================================

    print()
    print("=" * 70)

    print(
        "Finished successfully."
    )

    print("=" * 70)


# ============================================================
# ERROR HANDLING
# ============================================================

except requests.exceptions.ConnectionError:

    print()
    print(
        "ERROR: Could not connect to Tally."
    )

    print("""
Make sure:

1. Tally Prime is running.
2. The company is loaded.
3. Tally HTTP server is enabled.
4. Tally is listening on port 9000.
""")


except requests.exceptions.Timeout:

    print()
    print(
        "ERROR: Tally request timed out."
    )


except ET.ParseError as e:

    print()
    print(
        "ERROR: Tally returned invalid XML."
    )

    print(
        e
    )


except Exception as e:

    print()
    print(
        "Unexpected error:"
    )

    print(
        type(e).__name__,
        ":",
        e
    )