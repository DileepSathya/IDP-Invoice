#!/usr/bin/env python3
"""Print this machine's fingerprint — customer sends output to you for licensing."""

from __future__ import annotations

import sys

from licensing.hardware_fingerprint import collect_hardware_parts, machine_fingerprint


def _wait_for_exit() -> None:
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass


def main() -> None:
    if sys.platform != "win32":
        print("This tool supports Windows only.")
        _wait_for_exit()
        sys.exit(1)

    parts = collect_hardware_parts()
    fp = machine_fingerprint()
    print("IDP Invoice — Machine Fingerprint")
    print("=" * 40)
    print(f"Fingerprint (send this to your vendor):\n{fp}\n")
    print("Hardware summary (for support):")
    print(f"  MAC hash seed : {parts['mac']}")
    print(f"  CPU           : {parts['cpu'][:80]}")
    print(f"  Disk serial   : {parts['disk'][:80]}")
    print("\nCopy the fingerprint line above and email it to receive license.lic")
    _wait_for_exit()


if __name__ == "__main__":
    main()
