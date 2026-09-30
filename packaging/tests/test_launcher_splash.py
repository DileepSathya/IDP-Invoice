"""UI-independent behavior tests for the launcher startup window."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("idp_launcher", ROOT / "packaging" / "launcher.py")
assert SPEC and SPEC.loader
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class FakeRoot:
    def __init__(self) -> None:
        self.update_calls = 0
        self.destroyed = False

    def update_idletasks(self) -> None:
        self.update_calls += 1

    def update(self) -> None:
        self.update_calls += 1

    def destroy(self) -> None:
        self.destroyed = True


class FakeLabel:
    def __init__(self) -> None:
        self.text = ""

    def configure(self, *, text: str) -> None:
        self.text = text


class StartupWindowTests(unittest.TestCase):
    def test_startup_window_updates_status_and_closes_cleanly(self) -> None:
        root = FakeRoot()
        label = FakeLabel()
        window = launcher.StartupWindow(root, label)

        window.status("Starting API server…")
        window.close()

        self.assertEqual(label.text, "Starting API server…")
        self.assertEqual(root.update_calls, 2)
        self.assertTrue(root.destroyed)
