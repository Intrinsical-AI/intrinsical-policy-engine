# Authoring Errors

## Missing files or incompatible engine

Read the source path and field in the diagnostic, then check the format-3
`manifest.yml`. Its declared contract, runtime, profile, and
template paths must exist inside the pack. Its `requires_engine` constraint must
accept the installed engine version shown by `ipe --version`.

## Invalid condition or broken reference

Conditions are single-operator YAML mappings. Use only `has`, `any_prefix`,
`all`, `any`, and `not`; derivations cannot contain `not`. Check that referenced
flags, actions, evidence paths, and profile nodes are declared. `ipe lint`
validates these references and templates together.

Set action selection on `action.when`. `rules.packs`, `add_actions`, a separate
`ContractBundle.version`, and `reference_files` are removed contracts, without
aliases. The pack version belongs to `manifest.yml`.

## Rule does not fire

Inspect `_metadata/trace.json` and compare the completed flag set with the
structured condition. Flag names and prefixes must match exactly. Use
`ipe inspect --contracts PACK rule ID` to inspect a compiled rule. Also compare
`ipe inspect --contracts PACK` with the question or flag identifiers in the input.
Questions see only earlier answer emissions; derivations run afterward. An
unreachable positive question condition produces `QUESTION-UNREACHABLE`. Move
its activating question earlier or use the earlier question's direct flag.

## Invalid or ambiguous input

Supply exactly one of `answers` or `flags`. Check question IDs, value types,
allowed options, active questions, and typed metadata. Questions may be omitted;
a supplied non-null answer to an inactive question is rejected. Unknown fields
are errors. Metadata numbers must be finite, including nested values. Use the
checked JSON/YAML [input examples](CLI_REFERENCE.md#inputs-and-paths) as a small
starting point. Render question values through `answers` in templates.

## Rejected output destination

Assessment and export destinations must not exist yet, even as empty directories.
A seal archive must be a new path outside its input snapshot. Pick a fresh
destination such as `export-2` and `export-2.zip` for the next run.

An `OUTPUT-COLLISION` diagnostic instead identifies conflicting producers inside
the pack. Fix their relative paths or selection conditions. Pack nodes cannot
write under `_metadata`, `evidence`, `exports`, or `backlogs`, or claim the root
checksum/signature files, `evidence.zip`, or `compliance.ics`. Lint catches
reserved paths and unconditional collisions; export checks the complete selected
inventory before staging.

## Failed evidence check

`--check-ci` requires `--evidence-dir`. Check the selected evidence paths and
content in that directory. Pack templates are authoring material, and a status
field alone cannot satisfy evidence quality checks.

Inspect `_metadata/evidence_manifest.json` and `evidence_quality.json` in an
export created without the CI gate. Missing paths come from the selected
assessment requirements, not just the declared manifest. Correct the evidence
source and export to a new destination. Sealing never repairs missing evidence
or a changed checksum baseline; an assessment-only directory is not a complete
export snapshot.

For CSV, provide at least two nonblank header names and five nonblank data rows
of matching width to reach `ready`. Two to four data rows is `draft`; the header
and blank rows do not count. Malformed quoting fails. Markdown front matter is
metadata, including a closing delimiter at EOF; it contributes no body content.

## Invalid target configuration or relative dates

Start from a checked [target config](CLI_REFERENCE.md#target-configuration).
Select API targets explicitly and provide their required fields; a config alone
does not select targets. Use string IDs and one source of profile selection:
either config `profiles` or repeated `--profile` options. Paths passed on the CLI
resolve from the current working directory, not the config directory.
Nested IDs and tag/label mapping keys must be nonblank and free of control
characters.
HTTP header names must be nonempty ASCII tokens and values must contain no
control characters. Every emitted header value is redacted; supply actual
headers separately during delivery.

If the pack uses `T+Nd` or `T+Nm` dates, pass `--base-date YYYY-MM-DD` or declare
an explicit calendar base in the pack. The engine does not infer today's date.
Offsets and resolved dates must fit years 1–9999. An overflow is a contextual
validation error; reduce the offset or correct the base.

## Template or signing failure

Check unresolved template values with a minimal valid input and run lint again.
For signed seals, configure GPG and an available signing key. Choose `--no-sign`
explicitly when an unsigned archive is acceptable.

For optional pack metadata, guard `regulatory_meta is not none` before accessing
fields; `regulatory_warnings` is always a list. A missing snapshot format version
or disagreement between summary and trace requires a new export from original
inputs, without editing checksums. See [signing and recovery](CLI_REFERENCE.md#signing-and-recovery).

## Find a run without exposing inputs

Add `--log-jsonl /path/outside/inputs-and-outputs/commands.jsonl` after the
command. Use `run_id` to group lifecycle records and `exit_code` to locate the
failure. Logs omit raw input values and raw exception messages. Diagnostics
identify the source and field without echoing submitted values; use that
context when correcting the source file. Do not put logs inside a pack,
snapshot, evidence source, answer file, or config file, or at an output ZIP path.
See the [logging reference](CLI_REFERENCE.md#operational-logging).

The logger inserts a separator after a valid final JSON object without a newline.
An invalid or truncated trailing record is rejected without appending. Preserve
that log for diagnosis and choose a new log file; the engine never repairs it.
