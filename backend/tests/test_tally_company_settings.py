from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend import tally_company_settings


class FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    def find_one(self, query: dict) -> dict | None:
        doc_id = query.get("_id")
        if doc_id is None:
            return None
        doc = self.docs.get(doc_id)
        return dict(doc) if doc else None

    def update_one(self, query: dict, update: dict, upsert: bool = False) -> None:
        doc_id = query["_id"]
        if doc_id not in self.docs:
            if not upsert:
                return
            self.docs[doc_id] = {"_id": doc_id}
        if "$set" in update:
            self.docs[doc_id].update(update["$set"])


class TallyCompanySettingsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = TemporaryDirectory()
        self.bridge_root = Path(self.temp_dir.name) / "tally-bridge"
        self.bridge_root.mkdir()
        self.env_path = self.bridge_root / ".env"
        self.fake_collection = FakeCollection()

        self.root_patch = patch.object(
            tally_company_settings, "tally_bridge_root", return_value=self.bridge_root
        )
        self.db_patch = patch.object(
            tally_company_settings,
            "get_db",
            return_value={"erp_settings": self.fake_collection},
        )
        self.root_patch.start()
        self.db_patch.start()
        tally_company_settings._migration_done = False

    def tearDown(self) -> None:
        self.root_patch.stop()
        self.db_patch.stop()
        self.temp_dir.cleanup()

    def test_save_company_name_persists_to_mongodb(self) -> None:
        result = tally_company_settings.save_tally_company("  Amoga Industries  ")

        self.assertEqual(result, {"company_name": "Amoga Industries"})
        doc = self.fake_collection.docs["tally_company"]
        self.assertEqual(doc["company_name"], "Amoga Industries")
        self.assertIn("updated_at", doc)

    def test_current_company_reads_latest_value_without_process_restart(self) -> None:
        tally_company_settings.save_tally_company("First Company")
        self.assertEqual(
            tally_company_settings.get_tally_company(), {"company_name": "First Company"}
        )

        tally_company_settings.save_tally_company("Second Company")

        self.assertEqual(
            tally_company_settings.get_tally_company(), {"company_name": "Second Company"}
        )

    def test_save_company_name_rejects_empty_or_multiline_values(self) -> None:
        for company_name in ("", "   ", "Line one\nLine two", "bad\rvalue"):
            with self.subTest(company_name=company_name):
                with self.assertRaisesRegex(ValueError, "Company name"):
                    tally_company_settings.save_tally_company(company_name)

    def test_migrates_legacy_env_value_when_mongodb_is_empty(self) -> None:
        self.env_path.write_text(
            "# Tally connection\nTALLY_URL=http://localhost:9000\nTALLY_COMPANY=Legacy Company\n",
            encoding="utf-8",
        )

        self.assertEqual(
            tally_company_settings.current_tally_company(),
            "Legacy Company",
        )
        doc = self.fake_collection.docs["tally_company"]
        self.assertEqual(doc["company_name"], "Legacy Company")
        self.assertTrue(doc.get("migrated_from_env"))
        self.assertEqual(
            self.env_path.read_text(encoding="utf-8"),
            "# Tally connection\nTALLY_URL=http://localhost:9000\nTALLY_COMPANY=Legacy Company\n",
        )

    def test_mongodb_value_takes_priority_over_env_file(self) -> None:
        self.env_path.write_text("TALLY_COMPANY=Env Company\n", encoding="utf-8")
        tally_company_settings.save_tally_company("Mongo Company")

        self.assertEqual(
            tally_company_settings.current_tally_company(),
            "Mongo Company",
        )


if __name__ == "__main__":
    unittest.main()
