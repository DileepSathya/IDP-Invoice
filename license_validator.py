"""
Offline license validation for IDP Invoice (Windows / PyInstaller).

Place license.lic next to the application exe (install root).
invoice_count.enc is created automatically in the same folder.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import sys
from datetime import date, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from licensing.hardware_fingerprint import machine_fingerprint
from licensing.timestamps import parse_license_timestamp, utc_now_iso

logger = logging.getLogger(__name__)

_LICENSE_FILENAME = "license.lic"
_COUNTER_FILENAME = "invoice_count.enc"
_LICENSE_STATE_FILENAME = "license_state.json"
_LAUNCHER_FILENAME = "Start IDP Invoice.exe"
_KDF_SALT = b"IDP-Invoice-License-v1"
_KDF_ITERATIONS = 120_000

TIME_BASED_PLANS = frozenset({"monthly", "yearly"})
COUNT_BASED_PLANS = frozenset({"quota"})
UNLIMITED_PLANS = frozenset({"onetime"})
VALID_PLANS = TIME_BASED_PLANS | COUNT_BASED_PLANS | UNLIMITED_PLANS

_cached_payload: dict | None = None


class InvoiceQuotaExceeded(Exception):
    """Licensed invoice limit reached."""

    def __init__(self, message: str = "Invoice limit reached. Please upgrade.") -> None:
        super().__init__(message)
        self.message = message


def _license_disabled() -> bool:
    return os.environ.get("IDP_SKIP_LICENSE", "").strip().lower() in {"1", "true", "yes"}


def app_dir() -> Path:
    """Portable install root (license.lic / invoice_count.enc) or project root (dev)."""
    if getattr(sys, "frozen", False):
        current = Path(sys.executable).resolve().parent
        for directory in [current, *current.parents]:
            if (directory / _LICENSE_FILENAME).is_file():
                return directory
            if (directory / _LAUNCHER_FILENAME).is_file():
                return directory
        return current
    return Path(__file__).resolve().parent


def _fail(message: str) -> None:
    print(message)
    try:
        input("\nPress Enter to close...")
    except EOFError:
        pass
    sys.exit(1)


def _load_public_key():
    from licensing import public_key_embed

    pem = (public_key_embed.PUBLIC_KEY_PEM or "").strip()
    if not pem:
        _fail(
            "License system is not configured (missing public key). "
            "Contact support."
        )
    try:
        return serialization.load_pem_public_key(pem.encode("utf-8"))
    except Exception:
        _fail("License configuration error. Contact support.")


def _license_path() -> Path:
    return app_dir() / _LICENSE_FILENAME


def _counter_path() -> Path:
    return app_dir() / _COUNTER_FILENAME


def _license_state_path() -> Path:
    return app_dir() / _LICENSE_STATE_FILENAME


def _derive_aes_key(machine_id: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_KDF_SALT,
        iterations=_KDF_ITERATIONS,
    )
    return kdf.derive(machine_id.encode("utf-8"))


def _try_read_counter(machine_id: str) -> int | None:
    """Return decrypted count, or None if the file is missing or unreadable."""
    path = _counter_path()
    if not path.is_file():
        return None
    try:
        outer = json.loads(path.read_text(encoding="utf-8"))
        nonce = base64.b64decode(outer["nonce_b64"])
        ciphertext = base64.b64decode(outer["ciphertext_b64"])
        aes = AESGCM(_derive_aes_key(machine_id))
        plaintext = aes.decrypt(nonce, ciphertext, None)
        data = json.loads(plaintext.decode("utf-8"))
        return int(data.get("count", 0))
    except Exception:
        return None


def _write_counter(machine_id: str, count: int) -> None:
    aes = AESGCM(_derive_aes_key(machine_id))
    nonce = os.urandom(12)
    plaintext = json.dumps({"count": count}, separators=(",", ":")).encode("utf-8")
    ciphertext = aes.encrypt(nonce, plaintext, None)
    _counter_path().write_text(
        json.dumps(
            {
                "nonce_b64": base64.b64encode(nonce).decode("ascii"),
                "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
            },
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )


def _load_license_state() -> dict:
    path = _license_state_path()
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_license_state(state: dict) -> None:
    _license_state_path().write_text(
        json.dumps(state, separators=(",", ":"), sort_keys=True),
        encoding="utf-8",
    )


def _plan_name(payload: dict) -> str:
    return str(payload.get("plan", "")).strip().lower()


def _is_count_limited(payload: dict | None = None) -> bool:
    if _license_disabled():
        return False
    data = payload or _load_validated_payload()
    return _plan_name(data) in COUNT_BASED_PLANS


def _quota_period_start(payload: dict) -> datetime:
    issued_raw = str(payload.get("issuedAt", "")).strip()
    return parse_license_timestamp(issued_raw)


def _quota_period_key(payload: dict) -> str:
    blob = "|".join(
        (
            str(payload.get("customerId", "")).strip(),
            _plan_name(payload),
            str(payload.get("issuedAt", "")).strip(),
            str(payload.get("invoiceLimit", "")).strip(),
        )
    )
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _mongodb_invoice_count_since(period_start: datetime) -> int | None:
    try:
        from backend.agents.database import count_stored_invoices_since

        count = count_stored_invoices_since(period_start)
        if count < 0:
            return None
        return count
    except Exception as exc:
        logger.debug("MongoDB invoice count unavailable for quota reconcile: %s", exc)
        return None


def _reconcile_quota_counter(payload: dict) -> int:
    """
    Derive the effective invoice count for the current license period.

    - New license period: count MongoDB since issuedAt (ignore stale .enc from old period).
    - Same period: max(local .enc, MongoDB since issuedAt).
    - Rewrites invoice_count.enc when out of sync.
    """
    machine_id = str(payload.get("machineId", "")).strip().lower()
    period_start = _quota_period_start(payload)
    period_key = _quota_period_key(payload)
    state = _load_license_state()
    period_changed = state.get("periodKey") != period_key

    mongodb_count = _mongodb_invoice_count_since(period_start)
    enc_count = _try_read_counter(machine_id)

    if period_changed:
        effective = mongodb_count if mongodb_count is not None else 0
        if period_changed and enc_count is not None and mongodb_count is None:
            logger.warning(
                "License period changed; local usage cache reset (MongoDB unavailable)."
            )
    else:
        enc_val = enc_count if enc_count is not None else 0
        if mongodb_count is not None:
            effective = max(enc_val, mongodb_count)
            if enc_count is None and mongodb_count > 0:
                logger.info(
                    "Restored invoice usage counter from MongoDB (%s invoices this period).",
                    mongodb_count,
                )
            elif enc_count is not None and mongodb_count > enc_count:
                logger.info(
                    "Synced invoice usage counter from MongoDB (%s -> %s).",
                    enc_count,
                    mongodb_count,
                )
        else:
            effective = enc_val

    if enc_count != effective:
        _write_counter(machine_id, effective)

    if state.get("periodKey") != period_key:
        _save_license_state(
            {
                "periodKey": period_key,
                "issuedAt": str(payload.get("issuedAt", "")).strip(),
                "customerId": str(payload.get("customerId", "")).strip(),
            }
        )

    return effective


def _get_effective_invoice_count(payload: dict | None = None) -> int:
    data = payload or _load_validated_payload()
    if not _is_count_limited(data):
        return 0
    return _reconcile_quota_counter(data)


def _parse_license_file(path: Path) -> tuple[dict, bytes, bytes]:
    try:
        raw = path.read_text(encoding="utf-8").strip()
        wrapper = json.loads(base64.b64decode(raw).decode("utf-8"))
        payload_bytes = base64.b64decode(wrapper["payload_b64"])
        signature = base64.b64decode(wrapper["signature_b64"])
        payload = json.loads(payload_bytes.decode("utf-8"))
        return payload, payload_bytes, signature  # type: ignore[return-value]
    except Exception:
        _fail("License file is invalid or corrupted. Contact support.")


def _load_validated_payload() -> dict:
    global _cached_payload
    if _cached_payload is not None:
        return _cached_payload

    if _license_disabled():
        _cached_payload = {
            "customerId": "DEV",
            "machineId": machine_fingerprint(),
            "plan": "dev",
            "issuedAt": utc_now_iso(),
            "expiresAt": "2099-12-31",
            "invoiceLimit": 999_999_999,
        }
        return _cached_payload

    lic_path = _license_path()
    if not lic_path.is_file():
        _fail("License file not found. Contact support.")

    payload, payload_bytes, signature = _parse_license_file(lic_path)
    public_key = _load_public_key()
    try:
        public_key.verify(signature, payload_bytes, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature:
        _fail("License invalid.")
    except Exception:
        _fail("License invalid.")

    current_fp = machine_fingerprint()
    licensed_fp = str(payload.get("machineId", "")).strip().lower()
    if not licensed_fp or licensed_fp != current_fp.lower():
        _fail("License not valid for this machine.")

    plan = _plan_name(payload)
    if plan not in VALID_PLANS:
        _fail("License invalid.")

    expires_raw = str(payload.get("expiresAt", "")).strip()
    expires: date | None = None
    if expires_raw:
        try:
            expires = date.fromisoformat(expires_raw)
        except ValueError:
            _fail("License invalid.")

    if plan in TIME_BASED_PLANS:
        if expires is None:
            _fail("License invalid.")
        if date.today() > expires:
            _fail("License expired. Please renew.")
    elif plan == "quota":
        try:
            limit = int(payload.get("invoiceLimit", 0))
        except (TypeError, ValueError):
            _fail("License invalid.")
        if limit <= 0:
            _fail("License invalid.")
        issued_raw = str(payload.get("issuedAt", "")).strip()
        try:
            parse_license_timestamp(issued_raw)
        except ValueError:
            _fail("License invalid.")
        if expires is not None and date.today() > expires:
            _fail("License expired. Please renew.")
    elif plan == "onetime":
        pass

    _cached_payload = payload
    if _is_count_limited(payload):
        _reconcile_quota_counter(payload)
    return payload


def validate_license() -> bool:
    """Run all license checks. Exits the process on failure."""
    try:
        from dotenv import load_dotenv

        load_dotenv(app_dir() / ".env", override=False)
    except Exception:
        pass
    _load_validated_payload()
    return True


def get_remaining_days() -> int | None:
    """Days until expiry for time-based plans; None for other plan types."""
    payload = _load_validated_payload()
    plan = _plan_name(payload)
    if plan not in TIME_BASED_PLANS:
        return None
    expires_raw = str(payload.get("expiresAt", "")).strip()
    if not expires_raw:
        return None
    expires = date.fromisoformat(expires_raw)
    return max(0, (expires - date.today()).days)


def get_remaining_invoices() -> int:
    payload = _load_validated_payload()
    if _license_disabled() or not _is_count_limited(payload):
        return 999_999_999
    limit = int(payload.get("invoiceLimit", 0))
    count = _get_effective_invoice_count(payload)
    return max(0, limit - count)


def get_license_welcome_message() -> str:
    """Human-readable subscription status for launcher startup."""
    if _license_disabled():
        return "Development mode — license checks disabled."

    payload = _load_validated_payload()
    plan = _plan_name(payload)
    customer = str(payload.get("customerId", "")).strip()
    prefix = f"Licensed to {customer}. " if customer else ""

    if plan == "monthly":
        days = get_remaining_days() or 0
        expires = str(payload.get("expiresAt", "")).strip()
        day_word = "day" if days == 1 else "days"
        return (
            f"{prefix}Monthly subscription — {days} {day_word} remaining "
            f"(expires {expires})."
        )
    if plan == "yearly":
        days = get_remaining_days() or 0
        expires = str(payload.get("expiresAt", "")).strip()
        day_word = "day" if days == 1 else "days"
        return (
            f"{prefix}Yearly subscription — {days} {day_word} remaining "
            f"(expires {expires})."
        )
    if plan == "quota":
        remaining = get_remaining_invoices()
        limit = int(payload.get("invoiceLimit", 0))
        return f"{prefix}Quota subscription — {remaining} of {limit} invoices remaining."
    if plan == "onetime":
        return f"{prefix}One-time license — Enjoy unlimited processing."

    return f"{prefix}License active."


PLAN_LABELS = {
    "monthly": "Monthly Subscription",
    "yearly": "Yearly Subscription",
    "quota": "Quota Subscription",
    "onetime": "One-Time License",
}


def get_license_profile() -> dict:
    """Structured license details for the web UI (settings + dashboard banner)."""
    if _license_disabled():
        return {
            "plan": "dev",
            "planLabel": "Development Mode",
            "customerId": "",
            "issuedAt": None,
            "expiresAt": None,
            "remainingDays": None,
            "invoiceLimit": None,
            "invoicesUsed": None,
            "invoicesRemaining": None,
            "isUnlimited": True,
            "statusMessage": "Development mode — license checks disabled.",
        }

    payload = _load_validated_payload()
    plan = _plan_name(payload)
    customer = str(payload.get("customerId", "")).strip()
    issued = str(payload.get("issuedAt", "")).strip() or None
    expires_raw = str(payload.get("expiresAt", "")).strip()
    expires = expires_raw or None

    profile: dict = {
        "plan": plan,
        "planLabel": PLAN_LABELS.get(plan, "License"),
        "customerId": customer,
        "issuedAt": issued,
        "expiresAt": expires,
        "remainingDays": None,
        "invoiceLimit": None,
        "invoicesUsed": None,
        "invoicesRemaining": None,
        "isUnlimited": plan in UNLIMITED_PLANS or plan in TIME_BASED_PLANS,
        "statusMessage": "License active.",
    }

    if plan in TIME_BASED_PLANS:
        days = get_remaining_days() or 0
        profile["remainingDays"] = days
        day_word = "day" if days == 1 else "days"
        profile["statusMessage"] = f"{days} {day_word} remaining"
    elif plan == "quota":
        limit = int(payload.get("invoiceLimit", 0))
        remaining = get_remaining_invoices()
        used = max(0, limit - remaining)
        profile["invoiceLimit"] = limit
        profile["invoicesUsed"] = used
        profile["invoicesRemaining"] = remaining
        profile["isUnlimited"] = False
        profile["statusMessage"] = f"{remaining} invoice processing remaining"
    elif plan == "onetime":
        profile["statusMessage"] = "Unlimited invoice processing"

    return profile


def ensure_invoice_quota_available() -> None:
    """Raise InvoiceQuotaExceeded when no invoice slots remain (quota plan only)."""
    if not _is_count_limited():
        return
    if get_remaining_invoices() <= 0:
        raise InvoiceQuotaExceeded()


def increment_invoice_count() -> None:
    if _license_disabled() or not _is_count_limited():
        return
    payload = _load_validated_payload()
    limit = int(payload.get("invoiceLimit", 0))
    machine_id = str(payload.get("machineId", "")).strip().lower()

    mongodb_count = _mongodb_invoice_count_since(_quota_period_start(payload))
    if mongodb_count is not None:
        count = _reconcile_quota_counter(payload)  # sync enc from MongoDB
    else:
        count = (_try_read_counter(machine_id) or 0) + 1
        _write_counter(machine_id, count)

    if count > limit:
        raise InvoiceQuotaExceeded()
