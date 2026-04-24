from __future__ import annotations

import argparse
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv


def upload_one(
    *,
    api_base: str,
    api_key: str,
    file_path: Path,
    external_ref: str,
    callback_url: str | None,
    callback_secret: str | None,
) -> dict[str, Any]:
    url = f"{api_base.rstrip('/')}/v1/files"
    headers = {"X-API-Key": api_key}

    data: dict[str, str] = {"external_ref": external_ref}
    if callback_url:
        data["callback_url"] = callback_url
        data["callback_events"] = "job.completed,job.failed"
        if callback_secret:
            data["callback_secret"] = callback_secret

    with file_path.open("rb") as f:
        files = {"file": (file_path.name, f)}
        r = requests.post(url, headers=headers, data=data, files=files, timeout=120)
    r.raise_for_status()
    return r.json()


def main() -> int:
    load_dotenv()
    p = argparse.ArgumentParser(description="Upload one or more files to IDP API.")
    p.add_argument("--api-base", default=os.environ.get("API_BASE", "http://127.0.0.1:8000"))
    p.add_argument("--api-key", default=os.environ.get("API_KEY", "test-key-123"))
    p.add_argument("--callback-url", default=os.environ.get("CALLBACK_URL"))
    p.add_argument("--callback-secret", default=os.environ.get("WEBHOOK_SECRET"))
    p.add_argument("--concurrency", type=int, default=5)
    p.add_argument("files", nargs="+")
    args = p.parse_args()

    paths = [Path(x).expanduser().resolve() for x in args.files]
    for fp in paths:
        if not fp.exists() or not fp.is_file():
            raise SystemExit(f"File not found: {fp}")

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, int(args.concurrency))) as ex:
        futs = []
        for fp in paths:
            futs.append(
                ex.submit(
                    upload_one,
                    api_base=args.api_base,
                    api_key=args.api_key,
                    file_path=fp,
                    external_ref=fp.name,
                    callback_url=args.callback_url,
                    callback_secret=args.callback_secret,
                )
            )
        for fut in as_completed(futs):
            r = fut.result()
            results.append(r)
            print(f"uploaded job_id={r.get('job_id')} status={r.get('status')} webhook_enabled={r.get('webhook_enabled')}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())

