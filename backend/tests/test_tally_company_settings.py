from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend import tally_company_settings


class TallyCompanySettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.bridge_root = Path(self.temp_dir.name) / "tally-bridge"
        self.bridge_root.mkdir()
        self.env_path = self.bridge_root / ".env"
        self.root_patch = patch.object(
            tally_company_settings, "tally_bridge_root", return_value=self.bridge_root
        )
        self.root_patch.start()

    def tearDown(self) -> None:
        self.root_patch.stop()
        self.temp_dir.cleanup()

    def test_save_company_name_preserves_other_bridge_env_content(self) -> None:
        self.env_path.write_text(
            "# Tally connection\nTALLY_URL=http://localhost:9000\nTALLY_COMPANY=Old Company\n",
            encoding="utf-8",
        )

        result = tally_company_settings.save_tally_company("  Amoga Industries  ")

        self.assertEqual(result, {"company_name": "Amoga Industries"})
        self.assertEqual(
            self.env_path.read_text(encoding="utf-8"),
            "# Tally connection\n"
            "TALLY_URL=http://localhost:9000\n"
            "TALLY_COMPANY=Amoga Industries\n",
        )

    def test_current_company_reads_latest_value_without_process_restart(self) -> None:
        self.env_path.write_text("TALLY_COMPANY=First Company\n", encoding="utf-8")
        self.assertEqual(
            tally_company_settings.get_tally_company(), {"company_name": "First Company"}
        )

        self.env_path.write_text("TALLY_COMPANY=Second Company\n", encoding="utf-8")

        self.assertEqual(
            tally_company_settings.get_tally_company(), {"company_name": "Second Company"}
        )

    def test_save_company_name_rejects_empty_or_multiline_values(self) -> None:
        for company_name in ("", "   ", "Line one\nLine two", "bad\rvalue"):
            with self.subTest(company_name=company_name):
                with self.assertRaisesRegex(ValueError, "Company name"):
                    tally_company_settings.save_tally_company(company_name)


if __name__ == "__main__":
    unittest.main()
