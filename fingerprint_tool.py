#!/usr/bin/env python3
"""Print this machine's fingerprint — customer sends output to you for licensing."""

from __future__ import annotations

import sys

from licensing.hardware_fingerprint import HardwareIdentityError, machine_fingerprint


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

    print("IDP Invoice — Machine Fingerprint")
    print("=" * 40)
    try:
        fp = machine_fingerprint()
    except HardwareIdentityError as exc:
        print("Unable to create a machine fingerprint.")
        print(str(exc))
        print("CPU Processor ID and the Windows system-disk serial number are required.")
        _wait_for_exit()
        sys.exit(1)
    print(f"Fingerprint (send this to your vendor):\n{fp}\n")
    print("Hardware identity: CPU ID and Windows system disk detected.")
    print("\nCopy the fingerprint line above and email it to receive a license key.")
    print("Paste the key under Settings → Licensing after you log in to the dashboard.")
    _wait_for_exit()


if __name__ == "__main__":
    main()
