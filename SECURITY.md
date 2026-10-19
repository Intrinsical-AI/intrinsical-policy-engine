# Security Policy

## Scope and reporting

This repository contains the public engine library and CLI, an educational
starter, and an independent process mock with synthetic evidence.
See [BOUNDARIES.md](BOUNDARIES.md) for its publication boundary.

Report engine vulnerabilities to Pablo P.C. using the contact channel stated in
the repository profile or an applicable Order Form. Avoid public GitHub issues
for security-sensitive reports.

## Security model

Inputs may contain sensitive answers and identity metadata. Review authored
packs and templates, and intentionally choose whether to retain raw input with
`--include-raw-input`. Review generated artifacts before sharing them or sending
them to a delivery tool. The API exporters create offline records and replace
every configured HTTP header value with `<redacted>`, including values under
custom header names. Deliverers supply actual headers separately. Supplied
evidence bytes are preserved and require their own confidentiality review.

Sealing verifies the snapshot checksum inventory and signs it with GPG by
default. Explicit `--no-sign` seals are unsigned. Neither a checksum nor a
signature proves policy correctness, evidence truth, or external acceptance.
Snapshots use format version 1 and require identical trace content in the summary
and separate trace file. Checksums remain the original baseline; sealing never
repairs or regenerates them after drift.

The supported matrix is Linux with Python 3.11 and 3.12. Publication requires
exclusive `renameat2` support. See the [security and limitations guide](docs/SECURITY_AND_LIMITATIONS.md)
for trust boundaries and the [signing runbook](docs/CLI_REFERENCE.md#signing-and-recovery)
for key setup, verification, and interrupted-run handling.

## Supported versions and disclosure

Security fixes target the current published major version. Earlier majors are
not maintained. Confirmed vulnerabilities are disclosed in release notes after
a fix is available. The project does not currently operate a CVE assignment
process.
