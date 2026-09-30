import importlib.util
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from cryptography.hazmat.primitives.asymmetric import rsa

import license_validator as validator


def _load_website_generator():
    website_root = Path(__file__).resolve().parents[2].parent / "IDP-lic_website"
    module_path = website_root / "keygen" / "license_generator.py"
    spec = importlib.util.spec_from_file_location("website_license_generator", module_path)
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(website_root))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


class WebsiteInvoiceCompatibilityTests(unittest.TestCase):
    def test_website_signed_license_validates_in_invoice_application(self):
        generator = _load_website_generator()
        private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        fingerprint = "a" * 64
        payload = generator.build_license(
            customer_id="42",
            fingerprint=fingerprint,
            plan="monthly",
            invoice_limit=0,
            expires_at="2099-12-31",
            issued_at="2026-09-30T00:00:00Z",
        )
        license_blob = generator.sign_license(payload, private_key)
        parsed, payload_bytes, signature = validator._parse_license_blob(license_blob)

        with patch.object(validator, "_load_public_key", return_value=private_key.public_key()), patch.object(
            validator, "machine_fingerprint", return_value=fingerprint
        ):
            validated = validator._validate_payload(parsed, payload_bytes, signature)

        self.assertEqual(validated, payload)
        self.assertEqual(validated["machineId"], fingerprint)


if __name__ == "__main__":
    unittest.main()
