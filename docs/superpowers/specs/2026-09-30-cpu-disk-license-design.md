# CPU and System-Disk License Design

## Objective

`IDP-lic_website` is the only license issuer. It signs a customer-supplied
machine fingerprint and produces `license.lic`. `IDP_2.0-INVOICE` is the only
license consumer. It verifies the signature and enforces the machine binding,
plan expiry, and invoice quota.

Replace the existing MAC/CPU-name/WMIC/first-disk fingerprint completely. All
licenses produced after this change use one canonical fingerprint derived only
from the Windows CPU Processor ID and the serial number of the physical disk
that contains the Windows system drive.

Existing licenses created with the old fingerprint algorithm are intentionally
not compatible and must be regenerated.

## Required Behavior

A correctly installed, untampered license permits processing until one of these
commercial conditions occurs:

1. A quota plan has no remaining invoices.
2. A time-based plan has passed its expiry date.
3. The CPU or Windows system disk has changed, producing a different machine
   fingerprint.

A missing, corrupted, tampered, or incorrectly signed license is also rejected
as a security/configuration error. Failure to read hardware identity is reported
as an identity-read error, not as a machine mismatch.

Normal restarts, VPN use, network-adapter changes, adding or removing non-system
disks, and the absence of the deprecated `wmic.exe` command must not alter the
fingerprint.

## Canonical Fingerprint Algorithm

The shared hardware module performs the following operations:

1. Determine the Windows system drive from `SystemDrive`, falling back to the
   Windows directory drive.
2. Use PowerShell CIM queries to map that logical drive to its partition and
   then to the containing physical `Win32_DiskDrive`.
3. Read `Win32_Processor.ProcessorId` and the selected physical disk's
   `SerialNumber`.
4. Normalize each value by trimming leading/trailing whitespace, removing
   embedded whitespace, and converting to uppercase.
5. Reject missing identifiers and known placeholder identifiers rather than
   substituting an empty string.
6. Calculate lowercase hexadecimal SHA-256 over the UTF-8 string:

   `CPU=<NORMALIZED_CPU_ID>|DISK=<NORMALIZED_SYSTEM_DISK_SERIAL>`

Hardware reads use a bounded timeout and retry transient command failures. The
fingerprint tool and consumer use byte-for-byte identical shared logic.

## Issuer: IDP-lic_website

- Update the bundled fingerprint tool source to use the canonical algorithm.
- Display the fingerprint hash and a non-sensitive availability summary.
- Validate submitted fingerprint text as exactly 64 hexadecimal characters.
- Continue placing the normalized hash in the signed payload's `machineId`.
- Continue signing the canonical JSON payload with the vendor private key.
- Reject malformed fingerprints before writing a license or database record.
- Update website help text and licensing documentation to explain that changing
  the CPU or Windows system disk requires license regeneration.

The website does not receive or store raw CPU IDs or disk serials.

## Consumer: IDP_2.0-INVOICE

- Replace the hardware fingerprint module with the canonical algorithm.
- Validate the signed `machineId` as a 64-character hexadecimal fingerprint.
- Distinguish machine mismatch from hardware-identity read failure.
- Preserve RSA signature verification and the existing plan definitions.
- Re-evaluate time-based expiry when checking permission to process an invoice,
  so a process running across midnight cannot continue on an expired license.
- Enforce quota before processing and reconcile usage after successful storage.
- Keep UI profile/status reporting consistent with the same validation results.
- Update packaging and operational documentation as needed.

## Error Model

Validation produces specific internal failure reasons while showing concise
customer-facing messages:

- `license_missing`: license file not found.
- `license_corrupt`: wrapper or payload cannot be decoded.
- `signature_invalid`: signed content was modified or signed by another key.
- `identity_unavailable`: CPU ID or system-disk serial cannot be read.
- `machine_mismatch`: valid signed license belongs to another machine.
- `license_expired`: expiry date has passed.
- `quota_exhausted`: invoice allowance is zero.

Raw hardware identifiers and complete fingerprints are never logged. Diagnostic
logs may include component availability and a short fingerprint prefix.

## Testing

Shared fingerprint tests cover normalization, canonical hash construction,
missing values, placeholder values, PowerShell/CIM parsing, system-drive disk
selection, retries, and command failure.

Issuer tests cover valid fingerprint acceptance, malformed input rejection,
signed payload generation, and persistence only after successful generation.

Consumer tests cover valid signature, tampering, active and expired plans,
matching and mismatching machines, identity-read failure, quota availability,
and expiry checks performed after a payload has been cached.

An integration compatibility test uses a license generated by
`IDP-lic_website` and validates it in `IDP_2.0-INVOICE` with the same mocked CPU
ID and system-disk serial.

## Deployment and Migration

1. Deploy/rebuild the website fingerprint tool and license generator.
2. Rebuild the Invoice launcher, API, and watcher with the new consumer logic.
3. Have each customer run the new fingerprint tool.
4. Generate and install a new `license.lic` from the website.
5. Retire old fingerprint tools and old licenses; they are not accepted by the
   new application build.

The private signing key remains only in `IDP-lic_website`. The Invoice project
contains only the public verification key.

## Acceptance Criteria

- The same CPU and Windows system disk produce the same fingerprint across at
  least 100 consecutive reads and across application processes.
- Network changes and non-system disk changes do not change the fingerprint.
- Changing either mocked CPU ID or mocked system-disk serial changes it.
- A website-generated license validates in the Invoice application.
- An expired license and an exhausted quota are rejected at processing time.
- A hardware-read failure never appears as "license not valid for this machine."
- All new and existing non-licensing tests pass.
