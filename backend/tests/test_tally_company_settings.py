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

    def _save(self, **kwargs: object) -> dict:
        defaults = {
            "company_name": "Test Co",
            "tally_host": "127.0.0.1",
            "tally_port": 9000,
        }
        defaults.update(kwargs)
        return tally_company_settings.save_tally_company(
            str(defaults["company_name"]),
            str(defaults["tally_host"]),
            int(defaults["tally_port"]),  # type: ignore[arg-type]
        )

    def test_save_company_name_persists_to_mongodb(self) -> None:
        result = self._save(company_name="  Amoga Industries  ")

        self.assertEqual(
            result,
            {
                "company_name": "Amoga Industries",
                "tally_host": "127.0.0.1",
                "tally_port": 9000,
            },
        )
        doc = self.fake_collection.docs["tally_company"]
        self.assertEqual(doc["company_name"], "Amoga Industries")
        self.assertEqual(doc["tally_host"], "127.0.0.1")
        self.assertEqual(doc["tally_port"], 9000)
        self.assertIn("updated_at", doc)

    def test_current_company_reads_latest_value_without_process_restart(self) -> None:
        self._save(company_name="First Company")
        self.assertEqual(
            tally_company_settings.get_tally_company()["company_name"],
            "First Company",
        )

        self._save(company_name="Second Company")

        self.assertEqual(
            tally_company_settings.get_tally_company()["company_name"],
            "Second Company",
        )

    def test_save_company_name_rejects_empty_or_multiline_values(self) -> None:
        for company_name in ("", "   ", "Line one\nLine two", "bad\rvalue"):
            with self.subTest(company_name=company_name):
                with self.assertRaisesRegex(ValueError, "Company name"):
                    tally_company_settings.save_tally_company(
                        company_name,
                        "127.0.0.1",
                        9000,
                    )

    def test_save_rejects_invalid_host_or_port(self) -> None:
        with self.assertRaisesRegex(ValueError, "IP address"):
            self._save(tally_host="")
        with self.assertRaisesRegex(ValueError, "Port must be between"):
            self._save(tally_port=70000)

    def test_current_tally_url_builds_from_mongodb(self) -> None:
        self._save(tally_host="192.168.1.5", tally_port=9001)
        self.assertEqual(
            tally_company_settings.current_tally_url(),
            "http://192.168.1.5:9001",
        )

    def test_migrates_legacy_env_value_when_mongodb_is_empty(self) -> None:
        self.env_path.write_text(
            "# Tally connection\nTALLY_URL=http://localhost:9000\nTALLY_COMPANY=Legacy Company\n",
            encoding="utf-8",
        )

        self.assertEqual(
            tally_company_settings.current_tally_company(),
            "Legacy Company",
        )
        self.assertEqual(
            tally_company_settings.current_tally_url(),
            "http://localhost:9000",
        )
        doc = self.fake_collection.docs["tally_company"]
        self.assertEqual(doc["company_name"], "Legacy Company")
        self.assertEqual(doc["tally_host"], "localhost")
        self.assertEqual(doc["tally_port"], 9000)
        self.assertTrue(doc.get("migrated_from_env"))
        self.assertEqual(
            self.env_path.read_text(encoding="utf-8"),
            "# Tally connection\nTALLY_URL=http://localhost:9000\nTALLY_COMPANY=Legacy Company\n",
        )

    def test_mongodb_value_takes_priority_over_env_file(self) -> None:
        self.env_path.write_text(
            "TALLY_COMPANY=Env Company\nTALLY_URL=http://10.0.0.1:9000\n",
            encoding="utf-8",
        )
        self._save(company_name="Mongo Company", tally_host="192.168.0.2", tally_port=9000)

        self.assertEqual(
            tally_company_settings.current_tally_company(),
            "Mongo Company",
        )
        self.assertEqual(
            tally_company_settings.current_tally_url(),
            "http://192.168.0.2:9000",
        )


if __name__ == "__main__":
    unittest.main()
