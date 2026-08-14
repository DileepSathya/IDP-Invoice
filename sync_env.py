"""CLI wrapper — merge missing keys from .env.example into .env."""

from __future__ import annotations

from pathlib import Path

from backend.env_sync import sync_env_from_example


def main() -> int:
    root = Path(__file__).resolve().parent
    added = sync_env_from_example(root / ".env", root / ".env.example")
    if added == ["<created>"]:
        print(f"Created {root / '.env'} from .env.example")
    elif added:
        print(f"Added missing .env keys: {', '.join(added)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
