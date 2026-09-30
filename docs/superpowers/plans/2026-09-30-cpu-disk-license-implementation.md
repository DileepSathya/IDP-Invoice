# CPU and System-Disk License Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `IDP-lic_website` issue and `IDP_2.0-INVOICE` consume licenses bound deterministically to only the Windows CPU Processor ID and Windows system-disk serial number.

**Architecture:** Both repositories carry the same focused hardware-fingerprint module. It reads one JSON document from a bounded PowerShell CIM command, validates and normalizes CPU and system-disk identifiers, and hashes the canonical `CPU=...|DISK=...` string. The website validates the resulting hash before signing; the Invoice application distinguishes unavailable identity from an actual signed-machine mismatch and revalidates commercial policy at processing time.

**Tech Stack:** Python 3.11, Windows PowerShell/CIM, `hashlib`, `cryptography`, Flask, FastAPI, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-09-30-cpu-disk-license-design.md`

## Global Constraints

- Replace the old MAC/CPU-name/WMIC/first-disk algorithm completely; do not accept old fingerprints.
- Use only normalized CPU Processor ID and the physical serial of the disk containing the Windows system drive.
- Never substitute an empty string for unavailable hardware data.
- The website stores/signs only the SHA-256 fingerprint, never raw hardware identifiers.
- The Invoice repository contains only the public verification key; the private key remains in the website repository.
- Do not log raw CPU IDs, disk serials, or full fingerprints.

## Review Focus

- PowerShell emits a scalar rather than an array for one CPU/disk: parser must accept both shapes; Task 1 tests it.
- Virtual machines expose blank or placeholder identifiers: fingerprint generation must fail explicitly; Task 1 tests it.
- Multiple physical disks and removable disks are present: only the disk backing `SystemDrive` may be selected; Task 1 tests the returned query contract and rejects ambiguous output.
- A fingerprint pasted with spaces or non-hex characters reaches the website: generation must reject it before file/database writes; Task 2 tests it.
- An already-running process crosses the expiry boundary: invoice permission must be revalidated and denied; Task 3 tests it.

---

### Task 1: Canonical hardware fingerprint in both repositories

**Files:**
- Modify: `D:/IDP_2.0-INVOICE/licensing/hardware_fingerprint.py`
- Modify: `D:/IDP-lic_website/licensing/hardware_fingerprint.py`
- Create: `D:/IDP_2.0-INVOICE/backend/tests/test_hardware_fingerprint.py`
- Create: `D:/IDP-lic_website/tests/test_hardware_fingerprint.py`

**Interfaces:**
- Produces: `HardwareIdentityError(RuntimeError)`, `normalize_hardware_id(value: str, label: str) -> str`, `fingerprint_from_parts(cpu_id: str, disk_serial: str) -> str`, `collect_hardware_parts(*, runner=None, retries: int = 2) -> dict[str, str]`, and `machine_fingerprint(*, runner=None) -> str`.
- Consumes: no earlier task interfaces.

- [ ] **Step 1: Write failing Invoice tests** for exact normalization/hash output, empty and placeholder rejection, scalar/array JSON parsing, retry after command failure, and no MAC/network data in the canonical input.
- [ ] **Step 2: Run `pytest -q backend/tests/test_hardware_fingerprint.py`** and verify failures are caused by the missing canonical API/old MAC-based behavior.
- [ ] **Step 3: Implement the canonical module in Invoice** using one PowerShell script that maps `$env:SystemDrive` through CIM associations to exactly one `Win32_DiskDrive`, returns compressed JSON, and has a bounded timeout.
- [ ] **Step 4: Run `pytest -q backend/tests/test_hardware_fingerprint.py`** and verify all tests pass.
- [ ] **Step 5: Add equivalent website tests, run them red, copy the canonical module byte-for-byte, then run `pytest -q tests/test_hardware_fingerprint.py` green** from `D:/IDP-lic_website`.
- [ ] **Step 6: Verify `Get-FileHash` reports identical SHA-256 hashes for both hardware modules.**
- [ ] **Step 7: Commit each repository's Task 1 files** with `feat: use stable CPU and system-disk fingerprint`.

### Task 2: Website-only issuance and strict fingerprint validation

**Files:**
- Modify: `D:/IDP-lic_website/keygen/license_generator.py`
- Modify: `D:/IDP-lic_website/database/license_service.py`
- Modify: `D:/IDP-lic_website/templates/generate_license.html`
- Modify: `D:/IDP-lic_website/licensing/README.md`
- Create: `D:/IDP-lic_website/tests/test_license_issuance.py`
- Modify: `D:/IDP_2.0-INVOICE/fingerprint_tool.py`
- Modify: `D:/IDP_2.0-INVOICE/licensing/README.md`

**Interfaces:**
- Consumes: Task 1's lowercase 64-character `machine_fingerprint()` result.
- Produces: `normalize_fingerprint(value: str) -> str`, used by both `build_license` and `generate_license_file`; generated payloads keep `machineId` as the normalized hash.

- [ ] **Step 1: Write failing website tests** asserting uppercase valid hashes normalize to lowercase and blank, short, long, whitespace-containing, and non-hex fingerprints fail before `_load_private_key`, output-file creation, or database persistence.
- [ ] **Step 2: Run `pytest -q tests/test_license_issuance.py`** and verify the malformed-input cases currently reach generation.
- [ ] **Step 3: Implement `normalize_fingerprint` and call it at the generator/service boundary before side effects.**
- [ ] **Step 4: Update the website field copy and help text** to require the fingerprint produced by the downloadable tool and explain CPU/system-disk replacement.
- [ ] **Step 5: Update the Invoice fingerprint tool** to catch `HardwareIdentityError`, show only component availability plus the fingerprint, and never print raw CPU/disk identifiers.
- [ ] **Step 6: Run website issuance tests and the Invoice hardware tests** and verify both are green.
- [ ] **Step 7: Commit each repository's Task 2 files** with `feat: validate and issue canonical machine licenses` / `feat: update machine fingerprint tool`.

### Task 3: Consumer validation and processing-time policy enforcement

**Files:**
- Modify: `D:/IDP_2.0-INVOICE/license_validator.py`
- Create: `D:/IDP_2.0-INVOICE/backend/tests/test_license_validator.py`

**Interfaces:**
- Consumes: Task 1's `HardwareIdentityError` and `machine_fingerprint()`; Task 2's strict signed `machineId` format.
- Produces: customer-visible distinction among identity unavailable, machine mismatch, expiry, and quota exhaustion through existing `LicenseValidationError`, `LicenseInactiveError`, and `InvoiceQuotaExceeded` flows.

- [ ] **Step 1: Write failing tests** for malformed `machineId`, identity-read error mapping, true mismatch, valid match, a cached payload that expires before the next processing check, and quota exhaustion.
- [ ] **Step 2: Run `pytest -q backend/tests/test_license_validator.py`** and verify failures match the old generic/missing behavior.
- [ ] **Step 3: Implement strict fingerprint format validation and map `HardwareIdentityError` to the explicit identity-unavailable message without calling it a mismatch.**
- [ ] **Step 4: Ensure `ensure_invoice_quota_available()` force-refreshes signed payload validation and expiry before every processing entry point while preserving the development bypass.**
- [ ] **Step 5: Run validator tests and the full backend test suite** with `pytest -q backend/tests`; record every unrelated baseline failure if present.
- [ ] **Step 6: Commit** with `fix: make machine license validation deterministic`.

### Task 4: Cross-repository compatibility contract

**Files:**
- Create: `D:/IDP_2.0-INVOICE/backend/tests/test_license_cross_repo.py`
- Modify only if the test exposes a contract mismatch: website `keygen/license_generator.py`, Invoice `license_validator.py`, or public key embedding.

**Interfaces:**
- Consumes: Task 2's signed payload and Task 3's validator.
- Produces: an executable proof that a website-generated license is accepted by Invoice for matching hardware and rejected for different hardware.

- [ ] **Step 1: Write the cross-repository test** using a temporary RSA keypair, the website `sign_license` function, the Invoice parser/validator, and injected matching/mismatching Task 1 fingerprints.
- [ ] **Step 2: Run `pytest -q backend/tests/test_license_cross_repo.py`** and verify it fails before any compatibility correction.
- [ ] **Step 3: Apply the smallest contract correction, if required, without adding legacy compatibility.**
- [ ] **Step 4: Run the cross-repository, hardware, validator, and website issuance test files and verify all pass.**
- [ ] **Step 5: Commit** with `test: verify website and invoice license compatibility`.

### Task 5: Packaging, documentation, and final verification

**Files:**
- Modify if required: `D:/IDP_2.0-INVOICE/packaging/fingerprint_tool.spec`
- Modify: `D:/IDP_2.0-INVOICE/packaging/README.md`
- Modify: `D:/IDP-lic_website/licensing/README.md`

**Interfaces:**
- Consumes: all earlier task interfaces.
- Produces: documented rebuild/reissue procedure and packaged fingerprint tool dependencies.

- [ ] **Step 1: Add or update packaging assertions** so the fingerprint tool includes the canonical licensing module and no `wmic` dependency is documented.
- [ ] **Step 2: Run `node --test packaging/tests/test_release_packaging.cjs`** and verify any new assertion fails before documentation/spec changes.
- [ ] **Step 3: Update packaging and migration documentation** with the required order: rebuild tool and services, collect new fingerprint, issue on website, install new license, retire old license.
- [ ] **Step 4: Run all focused Python tests, `pytest -q backend/tests`, and packaging tests; verify module hashes are identical.**
- [ ] **Step 5: Perform 100 repeated fingerprint reads on this Windows host when hardware APIs are available; otherwise record the explicit identity-unavailable result without weakening validation.**
- [ ] **Step 6: Commit each repository's final documentation/packaging changes.**
- [ ] **Step 7: Review both repository diffs for secrets, raw hardware identifiers, private-key movement, unrelated edits, and old MAC/WMIC fingerprint references.**
