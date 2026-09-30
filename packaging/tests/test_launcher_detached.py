"""Behavior tests for the customer-facing Windows launcher."""

from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
LAUNCHER_PATH = ROOT / "packaging" / "launcher.py"
SPEC = importlib.util.spec_from_file_location("idp_launcher", LAUNCHER_PATH)
assert SPEC and SPEC.loader
launcher = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(launcher)


class DetachedProcessTests(unittest.TestCase):
    def test_background_service_has_no_console_without_discarding_standard_handles(self) -> None:
        """DETACHED_PROCESS makes console-based API logging lose its standard handles."""
        with patch.object(launcher.subprocess, "Popen") as popen:
            launcher._popen_cmd(["service.exe"], ROOT)

        kwargs = popen.call_args.kwargs
        flags = kwargs["creationflags"]
        self.assertTrue(flags & launcher.subprocess.CREATE_NO_WINDOW)
        self.assertTrue(flags & launcher.subprocess.CREATE_NEW_PROCESS_GROUP)
        self.assertFalse(flags & launcher.subprocess.DETACHED_PROCESS)

