# CLI Reference

Run `ipe --version` and `ipe COMMAND --help` to inspect the installed version
and command arguments. Checkout commands use `uv run --frozen ipe`; artifact
installations use their environment's `bin/ipe` directly. See
[installation](../README.md#install-a-wheel-or-source-distribution).

## Commands and defaults

| Command | Required arguments | Result |
| --- | --- | --- |
| `lint` | `--contracts PACK` | Compile contracts, references, and templates; diagnostics are JSON lines, and the status is printed to stderr. |
| `inspect` | `--contracts PACK` | Pack overview, rule catalog and calendar as JSON; append `rule ID`, `rule FAMILY ID` or `action ID` for a compiled item. |
| `assess` | `--contracts PACK --answers INPUT --out NEW_DIR` | Plan, trace, and root checksum inventory. |
| `export` | `--contracts PACK --answers INPUT --out NEW_DIR` | Assessment metadata, selected targets, evidence snapshot, and root checksum inventory. |
| `seal` | `--export-dir SNAPSHOT` | Verify baseline and evidence consistency, then sign with GPG by default. |

Errors always fail. `--strict` makes warnings fatal and is off by default.
`lint`, `assess`, `export`, and `seal` accept it. Inspect produces JSON;
human status messages and warnings use stderr where they would disrupt that JSON.
Rule families are `derivation`, `stop`, `role`, `tier`, `outcome`, `routing`,
`due`, and `metric`. An unqualified rule ID must identify exactly one family;
ambiguous IDs fail and list the families to choose from.

| Option | Applies to | Default and behavior |
| --- | --- | --- |
| `--check-ci` | assess, export | Off. Require explicit evidence, ready content for every required path, and selected profile coverage. |
| `--evidence-dir DIR` | assess, export | Omitted. Read selected evidence paths from this directory; export copies supplied bytes into its snapshot. |
| `--include-raw-input` | assess, export | Off. Include unredacted answers and user metadata in destinations. |
| `--base-date YYYY-MM-DD` | assess, export | Omitted. Required for relative calendar dates unless the pack declares an explicit base date. Absolute calendar dates need no base. |
| `--target TARGET ...` | export | `filesystem`. Choices: filesystem, asana, jira, linear; no repeated targets. |
| `--config FILE` | export | Omitted. Strict JSON/YAML target settings; selected API targets need their required fields. |
| `--profile ID` | export | All compiled profiles. Repeat for several; cannot also set config `profiles`. |
| `--output NEW_ZIP` | seal | Omitted: verify/sign in place without creating an archive. If supplied, create a new external ZIP. |
| `--no-sign` | seal | Off. Prevent new signing; an existing signature is still verified. |
| `--log-jsonl FILE` | all five | Omitted. Append operational events to an external JSONL log. Place the option after the command. |

An assessment contains no copied evidence snapshot. Seal an evidence-bearing
export, not the assessment directory. Missing required evidence, inconsistent
manifests, and checksum drift fail sealing regardless of warning strictness.
Snapshot metadata uses integer `snapshot_format_version: 1` in both summary and
trace; the summary's embedded trace must equal the separate trace file as JSON.
Older snapshots must be regenerated from their original inputs.
The starter has no deadlines. Selected actions with assigned due dates produce
`compliance.ics`.

## Inputs and paths

The `--answers` filename can contain exactly one of `answers` or `flags` in JSON
or YAML. Checked examples:

- [`answers.json`](../demos/starter/basic/answers.json) and
  [`answers.yml`](../demos/starter/basic/answers.yml): typed question answers.
- [`flags.json`](../demos/starter/basic/flags.json) and
  [`flags.yml`](../demos/starter/basic/flags.yml): declared flags, bypassing the questionnaire.

Unknown input fields, question IDs, flag names, option values, and nonfinite
metadata numbers fail validation. Questions may be omitted or null; a supplied
non-null answer to an inactive question fails. See
[the input schema](POLICY_SCHEMA.md#assessment-inputs).

CLI paths (`--contracts`, `--answers`, `--config`, `--evidence-dir`, `--out`,
`--log-jsonl`, `--export-dir`, `--output`) resolve from the process's current
working directory. They do not resolve relative to the input or config file.
Manifest paths resolve within the pack. Evidence requirements and rendered
destinations use canonical relative paths: `/` separators, no absolute paths,
`..`, `.` segments, repeated separators, or backslashes. A directory evidence
requirement includes descendants by path segment, as explained in
[the evidence schema](POLICY_SCHEMA.md#evidence).

Assessment/export destinations must not exist, even as empty directories or
symbolic links. ZIP paths must not exist and must lie outside their source
snapshot. Use new paths for each run; output protection never cleans an old run.
Pack-authored destinations additionally exclude engine-owned namespaces and
control paths. Lint checks reserved paths and unconditional collisions; export
preflights all selected producers before staging. See the
[output contract](POLICY_SCHEMA.md#output-and-snapshot-contract).

## Target configuration

Config keys are `uid_namespace`, `profiles`, `filesystem`, `asana`, `jira`, and
`linear`. Unknown keys fail. `uid_namespace` defaults to the pack's framework
ID and scopes action identities. `profiles` defaults to all pack profiles; a
provided list selects those IDs and replaces that default. Set profiles in
either the config or repeated CLI `--profile` options.

| Target | Required fields | Optional fields and defaults | Checked example |
| --- | --- | --- | --- |
| filesystem | None; object `{}` | None | [filesystem.yml](../demos/starter/configs/filesystem.yml) |
| asana | `base_url`, `project_gid` | `headers: {}`, `tag_gids: []`, `tag_gids_by_label: {}` | [asana.yml](../demos/starter/configs/asana.yml) |
| jira | `base_url`, `project_key` | `headers: {}`, `issue_type: Task`, `customfields: {}` | [jira.yml](../demos/starter/configs/jira.yml) |
| linear | `base_url`, `teamId` | `headers: {}`, `projectId: null`, `labelIds: []`, `labelIds_by_label: {}` | [linear.yml](../demos/starter/configs/linear.yml) |

All IDs and header values are strings. IDs, including nested lists and tag/label
mapping values, and tag/label mapping keys must be nonblank and contain no control
characters. Header names are nonempty ASCII HTTP tokens; header values cannot contain control
characters. API `base_url` must be an absolute
HTTP(S) URL with a valid host and optional port; inline credentials, whitespace,
and control characters are rejected. Jira `customfields` accepts finite JSON
values and cannot replace `project`, `issuetype`, `summary`, `description`,
`duedate`, or `labels`. Map tag/label names to provider IDs through the optional
mapping fields.

[`offline-all.json`](../demos/starter/configs/offline-all.json) combines all four
targets. A config does not select targets: use `--target` explicitly. For example:

```bash
target_run="$(mktemp -d "${TMPDIR:-/tmp}/ipe-targets.XXXXXX")"
uv run --frozen ipe export \
  --contracts frameworks/starter --answers demos/starter/basic/flags.yml \
  --evidence-dir demos/starter/basic/evidence --check-ci --strict \
  --config demos/starter/configs/offline-all.json \
  --target filesystem asana jira linear --out "$target_run/export"
```

The API targets emit offline records under `exports/<target>/requests.ndjson`
and `index.json`; Asana also writes `import.csv`. Their envelope action UIDs
agree across targets. Linear's UID stays in the envelope, not unsupported
GraphQL input fields. Provider delivery and provider acceptance are separate
operations. Example URLs and IDs are fictitious; no requests are sent.

Every configured header value is replaced with `<redacted>` in emitted records,
regardless of its name. A delivery tool must supply actual headers, including
ordinary headers such as content type, separately. Keep actual credentials out
of demo files. Default input redaction applies before
rendering and payload generation; supplied evidence bytes are retained exactly,
so review evidence independently before sharing the snapshot.

## Operational logging

```bash
log_run="$(mktemp -d "${TMPDIR:-/tmp}/ipe-logs.XXXXXX")"
uv run --frozen ipe lint --contracts frameworks/starter --strict \
  --log-jsonl "$log_run/commands.jsonl"
uv run --frozen ipe inspect --contracts frameworks/starter \
  --log-jsonl "$log_run/commands.jsonl"
```

Each invocation has a distinct `run_id`. Records contain `timestamp`, `run_id`,
`command`, `level`, `event`, and `data`. When lifecycle logging completes, a run
emits `command.start`, then `command.complete` or `command.failed`; the terminal record includes
`exit_code` and `duration_ms`. Failure events record error categories, not raw
input values or exception messages. Argument parsing errors occur before a
command starts.
An interrupted process can leave only the start record. Inspect any output before
retrying; a missing terminal event does not prove that nothing was published.

Logs and their adjacent lock files stay outside snapshots, packs, input files,
target config files, the supplied `--evidence-dir`, and any `--output` ZIP path.
Logs append across runs under an adjacent lock. A valid final JSON object without
a newline gets a separator before the next record. Invalid or truncated trailing
JSON without a newline fails without appending; preserve that log and use a new
path. This check does not validate earlier complete lines. Symbolic links are
rejected; new POSIX log files use mode `0600`. A log write failure is reported as
an error and can prevent a
lifecycle record from being written. If it occurs after the command finishes,
the output may already be published; inspect that output before choosing the
next destination. [Authoring errors](AUTHORING_ERRORS.md) explains how to act on
a failed command without exposing inputs.

## Signing and recovery

GPG uses the current user's keyring or `GNUPGHOME` and its default signing key.
The CLI has no signing-key selector. GPG subprocesses have a 30-second timeout,
and verification never retrieves keys automatically. Recipients must obtain the
public key and trusted fingerprint separately.

This Linux example creates a disposable, passphrase-free test key in a separate
keyring. Use a managed, protected key for actual releases. Run from the synced
checkout root:

```bash
sign_run="$(mktemp -d "${TMPDIR:-/tmp}/ipe-signing.XXXXXX")"
mkdir -m 700 "$sign_run/gnupg"
gpg --homedir "$sign_run/gnupg" --batch --pinentry-mode loopback \
  --passphrase '' --quick-generate-key 'IPE disposable example' ed25519 sign 1d
gpg --homedir "$sign_run/gnupg" --fingerprint 'IPE disposable example'
uv run --frozen ipe export \
  --contracts frameworks/starter --answers demos/starter/basic/answers.json \
  --evidence-dir demos/starter/basic/evidence --check-ci --strict \
  --out "$sign_run/export"
GNUPGHOME="$sign_run/gnupg" uv run --frozen ipe seal \
  --export-dir "$sign_run/export" --output "$sign_run/export.zip" --strict
gpg --homedir "$sign_run/gnupg" --batch --no-auto-key-retrieve --verify \
  "$sign_run/export/CHECKSUMS.sha256.asc" "$sign_run/export/CHECKSUMS.sha256"
```

Compare the fingerprint printed by `seal` with the test key's fingerprint.
Verification of the detached signature authenticates the checksum inventory;
`ipe seal` also verifies the payload and evidence consistency. An existing
signature is verified even with `--no-sign`. The option only disables creation
of a new signature. An unsigned archive identifies no signer.

On a failed or interrupted run:

1. Preserve the inputs, output path, external log, and exact command. Inspect
   whether an output was published before retrying.
2. For assessment/export failure, use a fresh destination after correcting the
   reported cause. The stable adjacent lock is coordination state and can remain.
3. For seal failure, verify the existing baseline and signature. Publication can
   leave a complete signature without a ZIP if the later ZIP step failed. Once
   verified, retry with a new external ZIP path and the required public key in
   the keyring.
4. If payload bytes, metadata, or evidence changed, export again from reviewed
   original inputs. Do not edit or recompute `CHECKSUMS.sha256` to erase drift.

These operations establish local integrity and signer possession. They do not
approve policy content or establish expert, pilot, provider, or release acceptance.

## Release runbook

CI builds one wheel and source distribution, then installs those exact bytes on
both supported interpreters. It retains the distributions, locked runtime
requirements, source hashes and validation receipts. The final
`reviewable-release-candidate` artifact binds the four installed results to that
single pair with `scripts/release_candidate.py`. Its manifest records automated
checks separately from outstanding human acceptance and does not authorize
publication. The workflow also supports manual dispatch for a release rehearsal.

The supported execution matrix is Linux with Python 3.11 and 3.12, including
exclusive `renameat2` output publication. macOS and Windows are not accepted
platforms. Use each declared interpreter for checks; a local run on one version
does not substitute for the matrix or remote CI.

From the final source revision, perform the development checks in the
[README](../README.md#packs-and-development), then build into a fresh location:

```bash
release_run="$(mktemp -d "${TMPDIR:-/tmp}/ipe-release.XXXXXX")"
uv lock --check
uv build --out-dir "$release_run/dist"
uv run --frozen python scripts/check_public_release.py \
  --distribution "$release_run/dist/intrinsical_policy_engine-3.0.0-py3-none-any.whl" \
  --distribution "$release_run/dist/intrinsical_policy_engine-3.0.0.tar.gz"
uv export --frozen --no-dev --no-emit-project \
  --output-file "$release_run/runtime-requirements.txt" --quiet
for artifact in "$release_run"/dist/*; do
  installed_run="$(mktemp -d "$release_run/installed.XXXXXX")"
  uv venv --python 3.12 "$installed_run/venv"
  uv pip install --python "$installed_run/venv/bin/python" --require-hashes \
    --requirements "$release_run/runtime-requirements.txt"
  uv pip install --python "$installed_run/venv/bin/python" --no-deps "$artifact"
  uv pip check --python "$installed_run/venv/bin/python"
  "$installed_run/venv/bin/python" scripts/check_installed_artifact.py \
    --ipe "$installed_run/venv/bin/ipe" --fixtures "$PWD" \
    --workdir "$installed_run/acceptance" --artifact "$artifact"
  "$installed_run/venv/bin/python" scripts/check_conformance.py \
    --ipe "$installed_run/venv/bin/ipe" --fixtures "$PWD" \
    --workdir "$installed_run/conformance" --artifact "$artifact"
done
```

Repeat with Python 3.11. The acceptance runner executes outside the checkout
using explicitly supplied fixture files. Packs, demos, docs directories, and
development scripts are external to both artifacts; the installed engine must
not import development helpers. The conformance runner copies the independent
mock and checks its ten expected scenarios through the installed CLI. Retain each
`acceptance.json`, `conformance.json`, artifact hashes, source revision, and
dirty-state record. Rebuild if source changes after testing.

Before publishing, inspect actual remote CI results, review the public-content
boundary, and record the owner accepting the release. Run signed acceptance if
signing is part of the release. Separately record any human mock walkthrough and
any provider sandbox exercise with their scope. Offline request generation is
not provider delivery; synthetic scenarios are not real pilots. Publish only
after the applicable acceptance evidence exists.

## Updating and maintenance handover

The v3 contract is a breaking replacement. Prefer a fresh environment, install
the reviewed artifact with its locked runtime requirements, and keep the old
environment available until the new walkthrough succeeds. Reauthor old packs
for format 3 and recreate snapshots from reviewed original inputs. The installed
namespace is `intrinsical_policy_engine`; retired `src` imports and old snapshot
formats are rejected. There is no in-place pack or snapshot migration command.

An upgrade rehearsal must name the previous distribution, its Git revision and
artifact hash, record the old version, install the candidate through the package
manager, and verify the new namespace and removal of the old package-owned
modules. Run installed acceptance and conformance after replacement. This proves
package replacement; unused dependencies can remain in an existing environment,
and the rehearsal does not approve old policy content or snapshots.

The handover consists of the candidate manifest, distributions, runtime lock,
matrix receipts, signing/recovery procedure, delivery-plan decisions and open
acceptances with their responsible roles. The release owner reviews actual
remote results for the intended revision before publication. Product/domain
owners accept the mock walkthrough and any separate real framework; the
independent reviewer records findings and disposition. Assign named people to
these roles before recording acceptance.

For support, capture the engine and pack versions, candidate/artifact hash,
operating system and Python version, exact command, expected and observed
behavior, and a minimal synthetic reproduction. Include redacted operational
logs and indicate whether a complete output or signature was published before
failure. Preserve the original checksum inventory. Use the repository issue
tracker for ordinary defects and [SECURITY.md](../SECURITY.md) for private
vulnerability reports. The supported matrix and published parser limits apply;
no production performance or response-time commitment is implied by the mock.
