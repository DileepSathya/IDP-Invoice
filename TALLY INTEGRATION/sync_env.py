"""CLI wrapper — merge missing keys from .env.example into .env."""

from __future__ import annotations

from pathlib import Path

from tally.env_sync import sync_env_from_example


def main() -> int:
    base = Path(__file__).resolve().parent
    added = sync_env_from_example(base / ".env", base / ".env.example")
    if added == ["<created>"]:
        print(f"Created {base / '.env'} from .env.example")
    elif added:
        print(f"Added missing .env keys: {', '.join(added)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
