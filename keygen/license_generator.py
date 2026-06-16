#!/usr/bin/env python3
"""Issue signed license.lic files for customers (run on your machine only)."""

from __future__ import annotations

import argparse
import base64
import csv
import json
import sys
from datetime import date, datetime
from pathlib import Path

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding

KEYGEN_DIR = Path(__file__).resolve().parent
REPO_ROOT = KEYGEN_DIR.parent
CUSTOMERS_DIR = REPO_ROOT / "Customers"
CUSTOMER_DATA_CSV = CUSTOMERS_DIR / "Customer_data.csv"
PRIVATE_KEY_PATH = KEYGEN_DIR / "private_key.pem"

PLAN_CHOICES = ("monthly", "yearly", "quota", "onetime")
CSV_HEADERS = (
    "generated_at",
    "customer_id",
    "fingerprint",
    "plan",
    "invoice_limit",
    "issued_at",
    "expires_at",
    "license_file",
)


def _load_private_key():
    if not PRIVATE_KEY_PATH.is_file():
        print(f"[ERROR] Missing {PRIVATE_KEY_PATH}. Run keygen/generate_keys.py first.")
        sys.exit(1)
    data = PRIVATE_KEY_PATH.read_bytes()
    return serialization.load_pem_private_key(data, password=None)


def build_license(
    *,
    customer_id: str,
    fingerprint: str,
    plan: str,
    invoice_limit: int,
    expires_at: str,
    issued_at: str | None = None,
) -> dict:
    return {
        "customerId": customer_id,
        "machineId": fingerprint.strip().lower(),
        "plan": plan,
        "issuedAt": issued_at or date.today().isoformat(),
        "expiresAt": expires_at,
        "invoiceLimit": int(invoice_limit),
    }


def sign_license(payload: dict, private_key) -> str:
    payload_bytes = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    signature = private_key.sign(payload_bytes, padding.PKCS1v15(), hashes.SHA256())
    doc = {
        "payload_b64": base64.b64encode(payload_bytes).decode("ascii"),
        "signature_b64": base64.b64encode(signature).decode("ascii"),
    }
    return base64.b64encode(json.dumps(doc, separators=(",", ":")).encode("utf-8")).decode("ascii")


def _validate_license_inputs(*, plan: str, invoice_limit: int, expires_at: str) -> None:
    if plan in {"monthly", "yearly"} and not expires_at:
        raise ValueError("--expires is required for monthly and yearly plans.")
    if plan == "quota" and invoice_limit <= 0:
        raise ValueError("Invoice limit must be > 0 for quota plan.")
    expires = expires_at.strip() or "2099-12-31"
    try:
        date.fromisoformat(expires)
    except ValueError as exc:
        raise ValueError("Expiry date must be YYYY-MM-DD.") from exc


def append_customer_record(
    *,
    customer_id: str,
    fingerprint: str,
    plan: str,
    invoice_limit: int,
    issued_at: str,
    expires_at: str,
    license_file: Path,
) -> None:
    CUSTOMERS_DIR.mkdir(parents=True, exist_ok=True)
    write_header = not CUSTOMER_DATA_CSV.is_file() or CUSTOMER_DATA_CSV.stat().st_size == 0
    with CUSTOMER_DATA_CSV.open("a", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=CSV_HEADERS)
        if write_header:
            writer.writeheader()
        writer.writerow(
            {
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "customer_id": customer_id,
                "fingerprint": fingerprint.strip().lower(),
                "plan": plan,
                "invoice_limit": invoice_limit,
                "issued_at": issued_at,
                "expires_at": expires_at,
                "license_file": str(license_file.resolve()),
            }
        )


def generate_license_file(
    *,
    customer_id: str,
    fingerprint: str,
    plan: str,
    invoice_limit: int = 0,
    expires_at: str = "",
    output: str = "",
) -> tuple[dict, Path]:
    plan = plan.strip().lower()
    if plan not in PLAN_CHOICES:
        raise ValueError(f"Invalid plan '{plan}'. Choose from: {', '.join(PLAN_CHOICES)}")

    expires = expires_at.strip() or "2099-12-31"
    limit = invoice_limit if plan == "quota" else 0
    _validate_license_inputs(plan=plan, invoice_limit=limit, expires_at=expires)

    payload = build_license(
        customer_id=customer_id.strip(),
        fingerprint=fingerprint,
        plan=plan,
        invoice_limit=limit,
        expires_at=expires,
    )
    private_key = _load_private_key()
    lic_body = sign_license(payload, private_key)

    if output.strip():
        out_path = Path(output.strip())
    else:
        CUSTOMERS_DIR.mkdir(parents=True, exist_ok=True)
        out_path = CUSTOMERS_DIR / f"{payload['customerId']}.lic"

    out_path.write_text(lic_body, encoding="utf-8")
    append_customer_record(
        customer_id=payload["customerId"],
        fingerprint=payload["machineId"],
        plan=plan,
        invoice_limit=payload["invoiceLimit"],
        issued_at=payload["issuedAt"],
        expires_at=payload["expiresAt"],
        license_file=out_path,
    )
    return payload, out_path


def _print_success(payload: dict, out_path: Path, plan: str) -> None:
    print(f"\n[OK] License written: {out_path.resolve()}")
    print(f"     Customer: {payload['customerId']}")
    print(f"     Machine:  {payload['machineId'][:16]}...")
    print(f"     Plan:     {payload['plan']}")
    if plan == "quota":
        print(f"     Limit:    {payload['invoiceLimit']} invoices")
    elif plan == "onetime":
        print("     Limit:    unlimited (one-time purchase)")
    else:
        print("     Limit:    unlimited (time subscription)")
    if plan in {"monthly", "yearly", "quota"}:
        print(f"     Expires:  {payload['expiresAt']}")
    else:
        print("     Expires:  none (perpetual)")
    print(f"     Record saved to: {CUSTOMER_DATA_CSV.resolve()}")
    print("\nCustomer must rename/copy this file to license.lic next to the application exe.")


def _prompt_required(label: str) -> str:
    while True:
        value = input(f"{label}: ").strip()
        if value:
            return value
        print("  This field is required.")


def _prompt_plan() -> str:
    print("\nPlan types:")
    print("  1) monthly  - time subscription (unlimited invoices until expiry)")
    print("  2) yearly   - time subscription (unlimited invoices until expiry)")
    print("  3) quota    - invoice count subscription")
    print("  4) onetime  - unlimited processing forever")
    aliases = {
        "1": "monthly",
        "2": "yearly",
        "3": "quota",
        "4": "onetime",
        "monthly": "monthly",
        "yearly": "yearly",
        "quota": "quota",
        "onetime": "onetime",
    }
    while True:
        choice = input("Enter plan (1-4 or name): ").strip().lower()
        plan = aliases.get(choice)
        if plan:
            return plan
        print("  Invalid plan. Enter 1, 2, 3, 4, or a plan name.")


def _prompt_expires(plan: str) -> str:
    if plan in {"monthly", "yearly"}:
        while True:
            value = _prompt_required("Expiry date (YYYY-MM-DD)")
            try:
                date.fromisoformat(value)
            except ValueError:
                print("  Invalid date. Use YYYY-MM-DD.")
                continue
            return value

    if plan == "quota":
        value = input("Expiry date (YYYY-MM-DD, optional — press Enter for none): ").strip()
        if not value:
            return "2099-12-31"
        try:
            date.fromisoformat(value)
        except ValueError:
            print("  Invalid date. Using 2099-12-31.")
            return "2099-12-31"
        return value

    return "2099-12-31"


def _prompt_limit(plan: str) -> int:
    if plan != "quota":
        return 0
    while True:
        raw = _prompt_required("Invoice limit")
        try:
            limit = int(raw)
        except ValueError:
            print("  Enter a whole number.")
            continue
        if limit <= 0:
            print("  Limit must be greater than 0.")
            continue
        return limit


def run_interactive() -> None:
    print("IDP Invoice — License Generator")
    print("=" * 40)

    customer_id = _prompt_required("Customer ID")
    fingerprint = _prompt_required("Machine fingerprint (from fingerprint_tool.exe)")
    plan = _prompt_plan()
    invoice_limit = _prompt_limit(plan)
    expires_at = _prompt_expires(plan)
    output = input(
        f"Output .lic path (optional — default: Customers\\{customer_id}.lic): "
    ).strip()

    try:
        payload, out_path = generate_license_file(
            customer_id=customer_id,
            fingerprint=fingerprint,
            plan=plan,
            invoice_limit=invoice_limit,
            expires_at=expires_at,
            output=output,
        )
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    _print_success(payload, out_path, plan)


def run_cli(args: argparse.Namespace) -> None:
    try:
        payload, out_path = generate_license_file(
            customer_id=args.customer,
            fingerprint=args.fingerprint,
            plan=args.plan,
            invoice_limit=args.limit,
            expires_at=args.expires,
            output=args.output,
        )
    except ValueError as exc:
        print(f"[ERROR] {exc}")
        sys.exit(1)

    _print_success(payload, out_path, args.plan.strip().lower())


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate a signed offline license file (.lic)")
    parser.add_argument("--customer", help="Customer ID string")
    parser.add_argument("--fingerprint", help="Machine fingerprint from customer")
    parser.add_argument(
        "--plan",
        choices=list(PLAN_CHOICES),
        help="monthly/yearly = time subscription; quota = invoice count; onetime = unlimited",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Invoice limit (required for quota plan only)",
    )
    parser.add_argument(
        "--expires",
        default="",
        help="Expiry date YYYY-MM-DD (required for monthly/yearly; optional for quota/onetime)",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Output .lic path (default: Customers/<customerId>.lic)",
    )
    args = parser.parse_args()

    if args.customer and args.fingerprint and args.plan:
        run_cli(args)
        return

    if any([args.customer, args.fingerprint, args.plan, args.output, args.expires, args.limit]):
        print("[ERROR] CLI mode requires --customer, --fingerprint, and --plan together.")
        sys.exit(1)

    run_interactive()


if __name__ == "__main__":
    main()
