# Security and Limitations

The engine evaluates authored policy. Its results require human review and do
not establish legal correctness, certification, or external acceptance.

## Inputs, destinations, and evidence

Input answers and identity metadata may contain confidential information.
Answer and user metadata values are redacted before export destinations receive
them. The explicit `--include-raw-input` option retains unredacted values; use it
only when that retention is intended. Review templates, target configuration, and
artifacts before sharing them.

Framework packs and templates are trusted authoring inputs. Review a pack before
executing it. Paths declared by the pack are constrained to the pack boundary;
export destinations must not exist yet. API targets require explicit typed
configuration and generate offline request files. Sending those files to a
provider is a separate operation with its own credentials and approval.
Every configured HTTP header value is replaced with `<redacted>` in those files,
regardless of its name. Delivery must supply actual headers separately. The
raw-input opt-in never disables header redaction. Nested provider IDs must be
nonblank, header names must be ASCII HTTP tokens, and IDs, tag/label mapping keys,
and header values cannot contain control characters.

`--check-ci` evaluates explicitly supplied evidence and copies it into the
snapshot. These content checks cannot establish that an evidence statement is
true. The included demo is a synthetic test fixture.
Evidence bytes are retained exactly and do not pass through input redaction.
The in-memory Python assessment API also returns raw identity metadata; callers
must apply an appropriate publication and retention boundary.

Quality checks inspect content structure and placeholder markers, including
nested JSON/YAML values and CSV cells. Simple markers match whole words.
For JSON/YAML, a top-level field counts toward the default three-field readiness
threshold only if its tree has a non-null leaf other than blank text or bytes;
`0` and `false` count, while null-only and empty wrappers do not.
Markdown front matter never counts as body content: delimiters may have surrounding
whitespace and use LF, CRLF, or CR line endings, including a closing delimiter at
EOF. An unclosed leading block has no classifiable body.

For authored documents, the shared JSON/YAML reader rejects duplicate keys, nonfinite floats, recursive
aliases, container nesting deeper than 64 levels, and more than 100,000 expanded
values, counting containers and scalar values. Acyclic YAML aliases remain valid
within those limits. These checks apply after parsing; they do not impose an
input-byte limit or guarantee a maximum parser memory allocation. Parser recursion
failures become controlled input errors.

CSV cells are limited to 131,072 characters; malformed quoting fails with a
diagnostic. A table needs at least two columns with nonblank names. Counted data
rows must match the header width. Entirely blank or whitespace-only rows do not count
toward quality thresholds; each counted row needs at least one nonblank cell.
The header never counts as a data row: fewer than two data rows is `placeholder`,
two to four is `draft`, and five or more is `ready`.

## Integrity and signing

The root `CHECKSUMS.sha256` inventories snapshot files. Sealing verifies that
inventory; `--output` also creates an external archive. Missing required evidence
or disagreement between the assessment, manifest, and actual evidence files
fails sealing even without `--strict`. Quality warnings become fatal with
`--strict`. GPG signing is the default and requires an available key;
`--no-sign` creates an unsigned archive explicitly.
Checksums and signatures establish integrity and signer possession, not the
truth or adequacy of the underlying assessment.

Snapshots have integer `snapshot_format_version: 1` in both summary and trace.
The summary's embedded trace must equal the separate trace document as JSON.
Generated snapshot metadata allows 128 container levels and 1,000,000 expanded
values to accommodate assessment wrappers; writers check these budgets before
publication, and sealing uses the same limits.
Missing or unsupported versions and inconsistent trace content fail verification.
The inventory is closed: added, removed, or modified payload files fail, and
sealing never silently recomputes a changed baseline. Re-export older or changed
snapshots from reviewed original inputs.

Export preflights the selected output inventory before staging. Pack nodes cannot
claim `_metadata`, `evidence`, `exports`, `backlogs`, root checksum/signature files,
`evidence.zip`, or `compliance.ics`. Lint also rejects reserved paths and
unconditional collisions. Cooperative locks coordinate engine writers; they do
not stop unrelated processes from modifying files. The source must remain under
the operator's control throughout export and sealing. A signature and ZIP publish
individually; a failed ZIP publication can leave a complete signature.
See the [signing and recovery runbook](CLI_REFERENCE.md#signing-and-recovery).

## Supported execution and acceptance limits

The supported matrix is Linux with Python 3.11 and 3.12. Directory publication
requires libc/kernel support for `renameat2` with no-replace semantics. The engine
fails when that operation is unavailable; it has no destructive rename fallback.
macOS and Windows are not accepted platforms, even though some low-level helpers
contain platform branches. Network and unusual filesystems require their own
validation of locking, hard links, and exclusive rename behavior.

The [EU AI Act process mock](../demos/process-review/README.md) is an independent
educational pack for generic intake, review, and signoff. Its fictional dates,
synthetic records, and stage labels have no legal fidelity. The complete framework
is maintained elsewhere and is not imported or reproduced. Local mock scenarios
demonstrate technical behavior and integrity, without proving real pilot results,
expert acceptance, or provider behavior.

Wheel and source distribution contain the engine, without packs, demos,
documentation directories, or development scripts. Installed acceptance obtains
fixtures from a separate checkout. A local green run does not prove remote CI,
publication, signing-key acceptance, or any external provider result. Keep those
receipts separate and identify the exact source and artifact tested.

## Public release scanner

`scripts/check_public_release.py` checks source paths, explicitly permitted text
file types, UTF-8 decoding, and a reserved-content denylist. Unknown types,
including HTML and nested archives, fail instead of being skipped. Supply each
wheel or source distribution with `--distribution` to scan its members too.
The scanner rejects escaping paths and archive links without extracting them.

This mechanical boundary check does not replace review for confidential content,
policy accuracy, external provider behavior, or release approval.
