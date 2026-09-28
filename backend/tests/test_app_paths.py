from pathlib import Path
import sys
import unittest
from unittest.mock import patch

from backend import app_paths


class FrozenAppPathTests(unittest.TestCase):
    def test_shared_services_resolve_the_install_root(self) -> None:
        executable = Path("C:/Users/customer/AppData/Local/Programs/IDP Invoice/idp-services/idp-api.exe")

        with patch.object(sys, "frozen", True, create=True), patch.object(
            sys, "executable", str(executable)
        ):
            self.assertEqual(executable.parent.parent, app_paths.app_dir())


if __name__ == "__main__":
    unittest.main()
