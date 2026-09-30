from __future__ import annotations

import sys
import types
import unittest
from unittest.mock import patch

database_module = types.ModuleType("backend.agents.database")
database_module.get_db = lambda: None
sys.modules.setdefault("backend.agents.database", database_module)

from backend import hitl_notification_settings


class FakeCollection:
    def __init__(self) -> None:
        self.docs: dict[str, dict] = {}

    def find_one(self, query: dict) -> dict | None:
        doc = self.docs.get(query["_id"])
        return dict(doc) if doc else None

    def update_one(self, query: dict, update: dict, upsert: bool = False) -> None:
        doc_id = query["_id"]
        if doc_id not in self.docs:
            assert upsert
            self.docs[doc_id] = {"_id": doc_id}
        self.docs[doc_id].update(update.get("$set", {}))


class HitlSmtpSettingsTests(unittest.TestCase):
    def test_smtp_settings_are_persisted_and_password_is_not_returned(self) -> None:
        collection = FakeCollection()
        with patch.object(hitl_notification_settings, "_collection", return_value=collection):
            result = hitl_notification_settings.save_hitl_smtp_settings(
                use_tls=True,
                host="smtp.example.com",
                port=587,
                user="mailer@example.com",
                password="secret-value",
                from_addr="sender@example.com",
            )

        self.assertEqual(
            result,
            {
                "use_tls": True,
                "host": "smtp.example.com",
                "port": 587,
                "user": "mailer@example.com",
                "from_addr": "sender@example.com",
                "configured": True,
            },
        )
        self.assertEqual(collection.docs["hitl_notifications"]["smtp_password"], "secret-value")
        self.assertNotIn("password", result)

    def test_smtp_settings_retain_password_when_blank_password_is_saved(self) -> None:
        collection = FakeCollection()
        collection.docs["hitl_notifications"] = {
            "_id": "hitl_notifications",
            "smtp_password": "old-secret",
        }
        with patch.object(hitl_notification_settings, "_collection", return_value=collection):
            hitl_notification_settings.save_hitl_smtp_settings(
                use_tls=False,
                host="smtp.example.com",
                port=25,
                user="",
                password="",
                from_addr="sender@example.com",
            )

        self.assertEqual(collection.docs["hitl_notifications"]["smtp_password"], "old-secret")
