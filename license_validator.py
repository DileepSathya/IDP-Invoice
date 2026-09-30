"""
License validation for IDP Invoice.

Signed license keys are stored in MongoDB (Settings → Licensing).
Quota usage is reconciled against MongoDB invoice counts.
Legacy license.lic files are migrated into MongoDB on first read.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
import os
import re
import sys
from datetime import date, datetime
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

from licensing.hardware_fingerprint import HardwareIdentityError, machine_fingerprint
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
FINGERPRINT_PATTERN = re.compile(r"^[0-9a-f]{64}$")

_cached_payload: dict | None = None
_cache_initialized = False
_validation_error: str | None = None


class InvoiceQuotaExceeded(Exception):
    """Licensed invoice limit reached."""

    def __init__(self, message: str = "Invoice limit reached. Please upgrade.") -> None:
        super().__init__(message)
        self.message = message


class LicenseValidationError(Exception):
    """License key failed cryptographic or policy checks."""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class LicenseInactiveError(Exception):
    """No valid active license — invoice processing is restricted."""

    def __init__(
        self,
        message: str = "No valid license. Add a license key under Settings → Licensing.",
    ) -> None:
        super().__init__(message)
        self.message = message


def _license_disabled() -> bool:
    return os.environ.get("IDP_SKIP_LICENSE", "").strip().lower() in {"1", "true", "yes"}


def app_dir() -> Path:
    """Portable install root or project root (dev)."""
    if getattr(sys, "frozen", False):
        current = Path(sys.executable).resolve().parent
        for directory in [current, *current.parents]:
            if (directory / _LICENSE_FILENAME).is_file():
                return directory
            if (directory / _LAUNCHER_FILENAME).is_file():
                return directory
        return current
    return Path(__file__).resolve().parent


def _load_public_key():
    from licensing import public_key_embed

    pem = (public_key_embed.PUBLIC_KEY_PEM or "").strip()
    if not pem:
        return None
    try:
        return serialization.load_pem_public_key(pem.encode("utf-8"))
    except Exception:
        return None


def _license_path() -> Path:
    return app_dir() / _LICENSE_FILENAME


def _counter_path() -> Path:
    return app_dir() / _COUNTER_FILENAME


def _derive_aes_key(machine_id: str) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=_KDF_SALT,
        iterations=_KDF_ITERATIONS,
    )
    return kdf.derive(machine_id.encode("utf-8"))


def _try_read_counter(machine_id: str) -> int | None:
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
    try:
        from backend.license_settings import get_period_state

        state = get_period_state()
        if state.get("periodKey"):
            return {
                "periodKey": state.get("periodKey"),
                "issuedAt": state.get("issuedAt"),
                "customerId": state.get("customerId"),
            }
    except Exception as exc:
        logger.debug("MongoDB license period state unavailable: %s", exc)

    path = app_dir() / _LICENSE_STATE_FILENAME
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _save_license_state(state: dict) -> None:
    try:
        from backend.license_settings import save_period_state

        save_period_state(
            period_key=str(state.get("periodKey", "")),
            issued_at=str(state.get("issuedAt", "")),
            customer_id=str(state.get("customerId", "")),
        )
    except Exception as exc:
        logger.debug("Could not persist license period state to MongoDB: %s", exc)

    legacy_path = app_dir() / _LICENSE_STATE_FILENAME
    try:
        legacy_path.write_text(
            json.dumps(state, separators=(",", ":"), sort_keys=True),
            encoding="utf-8",
        )
    except Exception:
        pass


def _plan_name(payload: dict) -> str:
    return str(payload.get("plan", "")).strip().lower()


def clear_license_cache() -> None:
    global _cached_payload, _cache_initialized, _validation_error
    _cached_payload = None
    _cache_initialized = False
    _validation_error = None


def _parse_license_blob(raw: str) -> tuple[dict, bytes, bytes]:
    normalized = "".join(str(raw or "").split())
    if not normalized:
        raise LicenseValidationError("License key is empty.")
    try:
        wrapper = json.loads(base64.b64decode(normalized).decode("utf-8"))
        payload_bytes = base64.b64decode(wrapper["payload_b64"])
        signature = base64.b64decode(wrapper["signature_b64"])
        payload = json.loads(payload_bytes.decode("utf-8"))
        return payload, payload_bytes, signature  # type: ignore[return-value]
    except LicenseValidationError:
        raise
    except Exception as exc:
        raise LicenseValidationError("License key is invalid or corrupted.") from exc


def _validate_payload(payload: dict, payload_bytes: bytes, signature: bytes) -> dict:
    public_key = _load_public_key()
    if public_key is None:
        raise LicenseValidationError(
            "License system is not configured (missing public key). Contact support."
        )
    try:
        public_key.verify(signature, payload_bytes, padding.PKCS1v15(), hashes.SHA256())
    except InvalidSignature as exc:
        raise LicenseValidationError("License invalid.") from exc
    except Exception as exc:
        raise LicenseValidationError("License invalid.") from exc

    licensed_fp = str(payload.get("machineId", "")).strip().lower()
    if not FINGERPRINT_PATTERN.fullmatch(licensed_fp):
        raise LicenseValidationError("License machine fingerprint format is invalid.")
    try:
        current_fp = machine_fingerprint()
    except HardwareIdentityError as exc:
        raise LicenseValidationError(
            "This machine's CPU or Windows system-disk identity is unavailable. "
            "License validation cannot continue."
        ) from exc
    if licensed_fp != current_fp.lower():
        raise LicenseValidationError("License not valid for this machine.")

    plan = _plan_name(payload)
    if plan not in VALID_PLANS:
        raise LicenseValidationError("License invalid.")

    expires_raw = str(payload.get("expiresAt", "")).strip()
    expires: date | None = None
    if expires_raw:
        try:
            expires = date.fromisoformat(expires_raw)
        except ValueError as exc:
            raise LicenseValidationError("License invalid.") from exc

    if plan in TIME_BASED_PLANS:
        if expires is None:
            raise LicenseValidationError("License invalid.")
        if date.today() > expires:
            raise LicenseValidationError("License expired. Please renew.")
    elif plan == "quota":
        try:
            limit = int(payload.get("invoiceLimit", 0))
        except (TypeError, ValueError) as exc:
            raise LicenseValidationError("License invalid.") from exc
        if limit <= 0:
            raise LicenseValidationError("License invalid.")
        issued_raw = str(payload.get("issuedAt", "")).strip()
        try:
            parse_license_timestamp(issued_raw)
        except ValueError as exc:
            raise LicenseValidationError("License invalid.") from exc
        if expires is not None and date.today() > expires:
            raise LicenseValidationError("License expired. Please renew.")
    elif plan == "onetime":
        pass

    return payload


def _maybe_migrate_license_file_to_mongodb() -> str | None:
    try:
        from backend.license_settings import get_stored_license_key, save_license_key

        if get_stored_license_key():
            return None
        lic_path = _license_path()
        if not lic_path.is_file():
            return None
        raw = lic_path.read_text(encoding="utf-8").strip()
        if not raw:
            return None
        save_license_key(raw)
        logger.info("Migrated legacy license.lic into MongoDB.")
        return "".join(raw.split())
    except Exception as exc:
        logger.debug("Legacy license.lic migration skipped: %s", exc)
        return None


def _load_license_key_from_mongodb() -> tuple[str | None, bool]:
    """Return (license_key, mongodb_reachable)."""
    try:
        from backend.license_settings import get_stored_license_key_with_status

        key, db_ok = get_stored_license_key_with_status()
        if not db_ok:
            return None, False
        if key:
            return key, True
        migrated = _maybe_migrate_license_file_to_mongodb()
        return migrated, True
    except Exception as exc:
        logger.debug("Could not read license key from MongoDB: %s", exc)
        return None, False


def refresh_license_cache(*, force: bool = False) -> None:
    """Load license key from MongoDB, validate, and update the in-memory cache."""
    global _cached_payload, _cache_initialized, _validation_error

    if _cache_initialized and not force:
        return

    if _license_disabled():
        _cache_initialized = True
        _cached_payload = {
            "customerId": "DEV",
            "machineId": machine_fingerprint(),
            "plan": "dev",
            "issuedAt": utc_now_iso(),
            "expiresAt": "2099-12-31",
            "invoiceLimit": 999_999_999,
        }
        _validation_error = None
        return

    raw_key, db_ok = _load_license_key_from_mongodb()
    if not db_ok:
        _cache_initialized = False
        _cached_payload = None
        _validation_error = "Could not load license from MongoDB. Will retry on next check."
        return

    if not raw_key:
        # Do not cache "missing key" — key may be saved via Settings while this process runs.
        _cache_initialized = False
        _cached_payload = None
        _validation_error = "No license key saved. Add one under Settings → Licensing."
        return

    try:
        payload, payload_bytes, signature = _parse_license_blob(raw_key)
        payload = _validate_payload(payload, payload_bytes, signature)
    except LicenseValidationError as exc:
        _cached_payload = None
        _validation_error = exc.message
        _cache_initialized = True
        return

    _cache_initialized = True

    _cached_payload = payload
    _validation_error = None
    if _plan_name(payload) in COUNT_BASED_PLANS:
        try:
            _reconcile_quota_counter(payload)
        except Exception as exc:
            logger.warning("Quota reconcile after license refresh failed: %s", exc)


def is_licensed() -> bool:
    if _license_disabled():
        return True
    refresh_license_cache()
    return _cached_payload is not None


def _is_count_limited(payload: dict | None = None) -> bool:
    if _license_disabled():
        return False
    refresh_license_cache()
    data = payload or _cached_payload
    if data is None:
        return False
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


def _get_effective_invoice_count(payload: dict) -> int:
    if not _is_count_limited(payload):
        return 0
    return _reconcile_quota_counter(payload)


def _load_validated_payload() -> dict:
    refresh_license_cache()
    if _license_disabled():
        return _cached_payload  # type: ignore[return-value]
    if _cached_payload is None:
        raise LicenseInactiveError(_validation_error or "No valid license.")
    return _cached_payload


def validate_license() -> bool:
    """Refresh license state from MongoDB. Never exits the process."""
    try:
        from dotenv import load_dotenv

        load_dotenv(app_dir() / ".env", override=False)
    except Exception:
        pass
    refresh_license_cache(force=True)
    return is_licensed()


def save_license_key(license_key: str) -> dict:
    """Validate, persist to MongoDB, refresh cache, and return the license profile."""
    payload, payload_bytes, signature = _parse_license_blob(license_key)
    _validate_payload(payload, payload_bytes, signature)

    from backend.license_settings import save_license_key as persist_key

    persist_key(license_key)
    clear_license_cache()
    refresh_license_cache(force=True)
    return get_license_profile()


def get_remaining_days() -> int | None:
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
    if _license_disabled():
        return 999_999_999
    refresh_license_cache()
    payload = _cached_payload
    if payload is None or not _is_count_limited(payload):
        return 999_999_999 if payload is not None else 0
    limit = int(payload.get("invoiceLimit", 0))
    count = _get_effective_invoice_count(payload)
    return max(0, limit - count)


def get_license_welcome_message() -> str:
    if _license_disabled():
        return "Development mode — license checks disabled."

    refresh_license_cache()
    if _cached_payload is None:
        return _validation_error or "No active license — add a key under Settings → Licensing."

    payload = _cached_payload
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


def _machine_fingerprint_for_profile() -> str:
    try:
        return machine_fingerprint()
    except HardwareIdentityError:
        return ""


def _inactive_profile(message: str) -> dict:
    return {
        "plan": "none",
        "planLabel": "No active license",
        "customerId": "",
        "issuedAt": None,
        "expiresAt": None,
        "remainingDays": None,
        "invoiceLimit": None,
        "invoicesUsed": None,
        "invoicesRemaining": None,
        "isUnlimited": False,
        "licensed": False,
        "validationError": message,
        "machineFingerprint": _machine_fingerprint_for_profile(),
        "statusMessage": message,
    }


def get_license_profile() -> dict:
    """Structured license details for the web UI (settings + dashboard banner)."""
    refresh_license_cache(force=True)

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
            "licensed": True,
            "validationError": None,
            "machineFingerprint": _machine_fingerprint_for_profile(),
            "statusMessage": "Development mode — license checks disabled.",
        }

    if _cached_payload is None:
        return _inactive_profile(
            _validation_error or "No valid license. Add a key under Settings → Licensing."
        )

    payload = _cached_payload
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
        "licensed": True,
        "validationError": None,
        "machineFingerprint": _machine_fingerprint_for_profile(),
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
    """Raise when invoice processing is not allowed (inactive license or quota exhausted)."""
    if _license_disabled():
        return
    refresh_license_cache(force=True)
    if _cached_payload is None:
        raise LicenseInactiveError(
            _validation_error or "No valid license. Add a license key under Settings → Licensing."
        )
    if _is_count_limited() and get_remaining_invoices() <= 0:
        raise InvoiceQuotaExceeded()


def increment_invoice_count() -> None:
    if _license_disabled() or not _is_count_limited():
        return
    payload = _load_validated_payload()
    limit = int(payload.get("invoiceLimit", 0))
    machine_id = str(payload.get("machineId", "")).strip().lower()

    mongodb_count = _mongodb_invoice_count_since(_quota_period_start(payload))
    if mongodb_count is not None:
        count = _reconcile_quota_counter(payload)
    else:
        count = (_try_read_counter(machine_id) or 0) + 1
        _write_counter(machine_id, count)

    if count > limit:
        raise InvoiceQuotaExceeded()
