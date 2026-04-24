from __future__ import annotations

import hmac
import hashlib
import json
import os
import time
from typing import Any

from fastapi import FastAPI, Request, Response
from dotenv import load_dotenv


app = FastAPI(title="IDP Webhook Receiver")

# Load client_service/.env if present
load_dotenv()

# Must match what you send in upload: callback_secret=...
WEBHOOK_SECRET = (os.environ.get("WEBHOOK_SECRET") or "").strip()


def _parse_signature(sig_header: str) -> tuple[int | None, str | None]:
    # Expected: "t=...,v1=..."
    parts: dict[str, str] = {}
    for part in (sig_header or "").split(","):
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            parts[k.strip()] = v.strip()
    t_raw = parts.get("t")
    v1 = parts.get("v1")
    try:
        t = int(t_raw) if t_raw is not None else None
    except Exception:
        t = None
    return t, v1


def verify_webhook_signature(*, secret: str, body: bytes, signature_header: str, tolerance_seconds: int = 300) -> bool:
    if not secret:
        return False
    ts, v1 = _parse_signature(signature_header)
    if ts is None or not v1:
        return False
    now = int(time.time())
    if abs(now - ts) > tolerance_seconds:
        return False
    base = str(ts).encode("utf-8") + b"." + body
    expected = hmac.new(secret.encode("utf-8"), base, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, v1)


@app.post("/idp/webhook")
async def idp_webhook(req: Request) -> Any:
    body = await req.body()
    sig = req.headers.get("x-webhook-signature") or ""

    if not verify_webhook_signature(secret=WEBHOOK_SECRET, body=body, signature_header=sig):
        return Response(content="invalid signature", status_code=401)

    payload = json.loads(body.decode("utf-8"))
    event = payload.get("event")
    job_id = (payload.get("job") or {}).get("job_id")
    print(f"[webhook] verified event={event} job_id={job_id}")
    return {"ok": True}

