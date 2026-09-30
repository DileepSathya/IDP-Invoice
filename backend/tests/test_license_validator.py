import unittest
from unittest.mock import patch

import license_validator as validator
from licensing.hardware_fingerprint import HardwareIdentityError


class _AcceptingPublicKey:
    def verify(self, *_args, **_kwargs):
        return None


def _payload(machine_id="a" * 64, expires="2099-12-31", plan="monthly"):
    return {
        "customerId": "42",
        "machineId": machine_id,
        "plan": plan,
        "issuedAt": "2026-09-30T00:00:00Z",
        "expiresAt": expires,
        "invoiceLimit": 0,
    }


class LicenseValidatorTests(unittest.TestCase):
    def setUp(self):
        validator.clear_license_cache()

    def _validate(self, payload):
        with patch.object(validator, "_load_public_key", return_value=_AcceptingPublicKey()):
            return validator._validate_payload(payload, b"payload", b"signature")

    def test_rejects_non_sha256_machine_id_before_hardware_query(self):
        with patch.object(validator, "machine_fingerprint") as fingerprint:
            with self.assertRaisesRegex(validator.LicenseValidationError, "fingerprint format"):
                self._validate(_payload(machine_id="legacy-fingerprint"))
            fingerprint.assert_not_called()

    def test_reports_hardware_read_failure_as_identity_unavailable(self):
        with patch.object(
            validator,
            "machine_fingerprint",
            side_effect=HardwareIdentityError("disk query failed"),
        ):
            with self.assertRaisesRegex(validator.LicenseValidationError, "identity is unavailable"):
                self._validate(_payload())

    def test_reports_real_fingerprint_difference_as_machine_mismatch(self):
        with patch.object(validator, "machine_fingerprint", return_value="b" * 64):
            with self.assertRaisesRegex(validator.LicenseValidationError, "not valid for this machine"):
                self._validate(_payload())

    def test_accepts_matching_machine_fingerprint(self):
        with patch.object(validator, "machine_fingerprint", return_value="a" * 64):
            self.assertEqual(self._validate(_payload())["customerId"], "42")

    def test_processing_check_force_refreshes_expiry_policy(self):
        validator._cached_payload = _payload()
        with patch.object(validator, "refresh_license_cache") as refresh, patch.object(
            validator, "_is_count_limited", return_value=False
        ):
            validator.ensure_invoice_quota_available()
        refresh.assert_called_once_with(force=True)

    def test_expired_license_is_rejected(self):
        with patch.object(validator, "machine_fingerprint", return_value="a" * 64):
            with self.assertRaisesRegex(validator.LicenseValidationError, "expired"):
                self._validate(_payload(expires="2000-01-01"))

    def test_inactive_profile_survives_hardware_read_failure(self):
        with patch.object(
            validator,
            "machine_fingerprint",
            side_effect=HardwareIdentityError("disk query failed"),
        ):
            profile = validator._inactive_profile("Identity unavailable")
        self.assertEqual(profile["machineFingerprint"], "")
        self.assertFalse(profile["licensed"])


if __name__ == "__main__":
    unittest.main()
