import json
import unittest
from unittest.mock import patch

from licensing import hardware_fingerprint as fingerprint


class HardwareFingerprintTests(unittest.TestCase):
    def test_normalizes_and_hashes_only_cpu_and_disk(self):
        self.assertEqual(
            fingerprint.fingerprint_from_parts(" ab c123 ", " disk 987\n"),
            "8aea289b7577613b5804540488393070caf4ba16dc1331e2d449fe19ce517970",
        )

    def test_rejects_missing_or_placeholder_identifiers(self):
        for value in ("", "   ", "unknown", "Default String", "0" * 16):
            with self.subTest(value=value):
                with self.assertRaises(fingerprint.HardwareIdentityError):
                    fingerprint.normalize_hardware_id(value, "CPU Processor ID")

    def test_collects_scalar_json_values(self):
        calls = []

        def runner(script):
            calls.append(script)
            return json.dumps({"cpuId": " cpu-1 ", "diskSerial": " disk-1 "})

        parts = fingerprint.collect_hardware_parts(runner=runner)

        self.assertEqual(parts, {"cpu": "CPU-1", "disk": "DISK-1"})
        self.assertEqual(len(calls), 1)
        self.assertNotIn("mac", parts)

    def test_accepts_single_item_arrays_from_powershell(self):
        def runner(_script):
            return json.dumps({"cpuId": ["CPU-1"], "diskSerial": ["DISK-1"]})

        self.assertEqual(
            fingerprint.collect_hardware_parts(runner=runner),
            {"cpu": "CPU-1", "disk": "DISK-1"},
        )

    def test_retries_transient_hardware_query_failure(self):
        attempts = 0

        def runner(_script):
            nonlocal attempts
            attempts += 1
            if attempts == 1:
                raise RuntimeError("CIM temporarily unavailable")
            return json.dumps({"cpuId": "CPU-1", "diskSerial": "DISK-1"})

        parts = fingerprint.collect_hardware_parts(runner=runner, retries=2)

        self.assertEqual(parts["cpu"], "CPU-1")
        self.assertEqual(attempts, 2)

    def test_rejects_ambiguous_hardware_query_results(self):
        for field, value in (("cpuId", ["CPU-1", "CPU-2"]), ("diskSerial", ["D1", "D2"])):
            document = {"cpuId": "CPU-1", "diskSerial": "DISK-1"}
            document[field] = value
            with self.subTest(field=field):
                with self.assertRaises(fingerprint.HardwareIdentityError):
                    fingerprint.collect_hardware_parts(
                        runner=lambda _script, payload=document: json.dumps(payload),
                        retries=1,
                    )

    def test_default_hardware_query_is_cached_for_process_lifetime(self):
        fingerprint.clear_machine_fingerprint_cache()
        payload = json.dumps({"cpuId": "CPU-1", "diskSerial": "DISK-1"})
        with patch.object(fingerprint, "_run_powershell", return_value=payload) as runner:
            first = fingerprint.machine_fingerprint()
            second = fingerprint.machine_fingerprint()
        self.assertEqual(first, second)
        self.assertEqual(runner.call_count, 1)


if __name__ == "__main__":
    unittest.main()
