# Policy and Input Schema

Pack format 3 declares its layout and runtime requirements in `manifest.yml`.
The pack owns flags, questions, rules, actions, evidence mappings, calendar
rules, delivery profiles, and templates. The starter is version `0.3.0` and is
educational. The [process mock](../demos/process-review/README.md) is a separate
pack at `demos/process-review/pack`; its EU AI Act title describes a technical
exercise, with no legal fidelity or coupling to the framework kept elsewhere.

Unknown fields are rejected. The manifest is the single owner of the pack
version; `ContractBundle` has no separate `version`. Use `action.when` for
selection.

## Conditions

Every condition is a YAML mapping with exactly one of five operators:

| Operator | Value | Meaning |
| --- | --- | --- |
| `has` | declared flag name | The flag is present. |
| `any_prefix` | nonempty namespace | A present flag equals the namespace or starts with the namespace plus `.`. |
| `all` | list of conditions | Every child condition matches; an empty list is true. |
| `any` | list of conditions | At least one child condition matches; an empty list is false. |
| `not` | one condition | The child condition does not match. |

```yaml
when:
  all:
    - has: starter.needs_review
    - any:
        - has: starter.automated_decision
        - has: starter.sensitive_data
```

Actions, derivations, questions, profiles, and profile nodes default `when` to
true when omitted. Stops, classifiers, routing rules, and metrics signals require
an explicit `when`. Condition strings, booleans,
unknown operators, and mappings with several operators are invalid. `has`
references declared flags. Namespace conditions must match declared flags; use
`{any_prefix: starter}` without a trailing dot.

Derivations only use positive conditions: `has`, `any_prefix`, `all`, and `any`.
They add flags until closure is reached. `not` is allowed in selection and stop
conditions, where evaluation uses the completed flag set.

```yaml
derivations:
  - id: STARTER-DERIVE-REVIEW
    when:
      any:
        - has: starter.automated_decision
        - has: starter.sensitive_data
    set_flags:
      - starter.needs_review
# In actions.yml:
actions:
  - id: STARTER-CONTROL-REVIEW
    title: Review the control
    when:
      has: starter.needs_review
    evidence:
      - starter/control-review.md
```

`derivations` belongs to the rules contract; `actions` belongs to the actions
contract. They are shown together only to illustrate the connection.

## Evaluation order

1. Validate the input envelope and evaluate questions in declaration order, or
   use the explicitly supplied flags.
2. Compute positive derivations to closure.
3. Evaluate the first matching stop in declaration order. A stop selects no
   actions and supplies the terminal outcome.
4. Otherwise collect matching roles, select the first matching tier by descending
   priority, and collect matching outcome rules. Equal tier priorities retain
   declaration order. No matching tier yields `null`; no matching outcome yields
   an empty list.
5. Select actions whose own `when` matches, preserve their declaration order,
   apply role filtering, and remove actions excluded by the chosen route. Routing
   uses the first matching rule, then its declared default or `null`.
6. Resolve calendar values and apply due rules by descending priority. The first
   matching due rule owns each action; its earliest calendar key is the deadline.
   Equal priorities retain declaration order.

Question conditions see only flags emitted by earlier answers. They cannot see
later answers or derived flags. Place activating questions first and use their
direct emissions in later question conditions. `lint` warns when a positive
question condition cannot match even the union of earlier option emissions;
this conservative check does not prove that every other condition is reachable.

Calendar values are ISO `YYYY-MM-DD` dates or nonnegative `T+Nd` / `T+Nm`
offsets. Relative dates require an explicit pack or CLI base; a CLI base overrides
the pack base. Month offsets clamp to the final day of the target month.
Values outside years 1–9999 are rejected. No date is inferred from today's date.

## Assessment inputs

Use exactly one input source in a JSON or YAML document:

```json
{"answers": {"STARTER_Q1": "yes", "STARTER_Q2": "yes", "STARTER_Q3": "yes"}}
```

Or supply declared flags directly:

```json
{"flags": ["starter.automated_decision"]}
```

Question IDs, answer types, allowed options, and question activation are validated
against the pack. Questions may be omitted or supplied as null; a non-null
answer to an inactive question is rejected. An explicit empty mapping or list
is distinct from an omitted input source. Optional metadata is typed under `system`, `provider`,
`owner`, `declared_by`, `approvals`, and `meta`. Metadata values must be JSON
data with finite numbers, including in nested mappings and lists. Unknown
top-level input fields are rejected.

For authored documents, the shared JSON/YAML reader rejects duplicate keys, nonfinite floats, cyclic
aliases, nesting beyond 64 container levels, and more than 100,000 expanded
container/scalar values. Acyclic aliases are accepted within these limits.
These are post-parse structural limits; the reader does not enforce an input-byte
cap. See [security limits](SECURITY_AND_LIMITATIONS.md).

Templates access question values through the single `answers` mapping. The
engine sanitizes sensitive values before destination rendering. Raw input is
retained only with the explicit `--include-raw-input` option.

Filesystem templates always receive `regulatory_warnings` as a list and
`regulatory_meta` as either the authored metadata mapping or `none`. Test
`regulatory_meta is not none` before reading its fields. These fields report
authored pack metadata, without establishing that its interpretation is correct.

## Evidence

For each selected action requiring evidence, required paths combine its direct
`evidence`, its action entry in `evidence_map`, and the evidence mappings of its
linked articles. Article links are the union of `Action.articles` and
`Article.related_actions`; either declaration establishes the link.

`--check-ci` validates explicit evidence from `--evidence-dir`. Every required
path needs a matching ready file, and every collected file must pass its quality
check. Paths are canonical relative paths: a directory requirement `starter`
matches `starter/review.md` and its nested descendants, never `starter-other`.
The exported manifest records included files and missing requirements derived
from the assessment and the actual supplied bytes.

Pack evidence templates describe expected authoring content. They do not replace
the supplied documents copied into the export snapshot. A template or a declared
status alone does not demonstrate that a requirement was fulfilled.

CSV evidence needs at least two nonblank header names. The header does not count
toward data thresholds: fewer than two nonblank, correctly sized data rows is
`placeholder`, two to four is `draft`, and five or more is `ready`. Blank rows
are ignored, malformed quoting fails, and cells are limited to 131,072 characters.
Markdown front matter contributes no body content, including when its closing
delimiter is at EOF. See [quality limits](SECURITY_AND_LIMITATIONS.md).

## Profile coverage

Delivery nodes declare action IDs in `trace_back_to.actions` and article IDs in
`trace_back_to.articles`. Each ID must exist in its own catalog. The `articles`
field accepts neither evidence paths nor IDs declared only as actions.

Coverage credits selected file and copy nodes whose `counts_for_coverage` is
true. Directory nodes do not provide coverage; an inactive node, parent, or
profile excludes its descendants. Every selected action requiring evidence and
every article in the assessment's article overlay must be covered for
`--check-ci`. Export checks profile coverage when the filesystem target is
selected. Missing coverage is reported by `CI-004` as `actions=` and `articles=`.

## Output and snapshot contract

Pack-authored outputs cannot claim engine namespaces `_metadata`, `evidence`,
`exports`, or `backlogs`, or root paths `CHECKSUMS.sha256`,
`CHECKSUMS.sha256.asc`, `evidence.zip`, or `compliance.ics`. Lint rejects reserved
paths and unconditional collisions. Export checks the complete selected inventory
before staging, including conditional nodes, file/directory conflicts, evidence,
and target outputs. Fix the conflicting producers in the pack; choosing another
output directory does not resolve an internal collision.

Serialized `_metadata/summary.json` and `_metadata/trace.json` both contain
integer `snapshot_format_version: 1`. The summary's embedded `trace` must equal
the separate trace document as JSON, including numeric and boolean types.
Generated snapshot metadata has separate limits of 128 container levels and
1,000,000 expanded values; writers validate them before publication, and sealing
uses the same budgets.
Sealing rejects missing, older, or unsupported versions and mismatched traces.
Recreate older snapshots from their original inputs; do not edit their inventory
or metadata to make them appear current.

The root checksum inventory is closed: added, removed, and changed payload files
fail verification. `CHECKSUMS.sha256` and its detached signature are control files
outside the payload inventory. Sealing verifies the original baseline and does
not calculate a new one after drift. An assessment directory has metadata and
checksums but lacks an export's evidence manifest and copied evidence.

## Python API

Imports use `intrinsical_policy_engine`, installed from `src/`; `scripts` is not
runtime API. Wheel and source distributions
include the `py.typed` marker so external type checkers can use the package's
inline annotations. Static types complement the runtime contract checks below.
From the synced checkout:

```python
from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import YamlContractsAdapter
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle

pack = YamlContractsAdapter().compile("frameworks/starter")
plan = assess_from_bundle(pack.bundle, {"flags": ["starter.automated_decision"]})
assert plan["actions"] == ["STARTER-CONTROL-REVIEW"]
assert plan["trace"]["framework_version"] == pack.bundle.manifest.version
```

`compile()` validates source layout, types, semantic references, profiles, paths,
and template context roots. Programmatic `ContractBundle` construction validates
types; `assess_from_bundle()` also applies the shared semantic validation,
including the engine requirement and canonical evidence paths. Directly authored
profiles and filesystem/template inputs still need compilation's IO checks.

The returned plan is an in-memory domain result, which can contain raw identity
metadata; it is not a publication-safe artifact. CLI assessment and export apply
projection before writing. `include_full_trace=True` also retains semantic input
in the in-memory trace. Callers own access and retention of that data. Snapshot
format fields are added by the artifact writer rather than this pure assessment.

## Generated JSON Schemas

[`docs/schemas`](schemas/index.json) contains deterministic schemas generated
from the current Pydantic models. Its index describes the covered contracts and
validation limits. Check synchronization without modifying files:

```bash
uv run --frozen python scripts/export_schemas.py --check --out-dir docs/schemas
```

After intentionally changing models, regenerate with the same command without
`--check` and review the diff. JSON Schema captures structural constraints;
semantic references, version compatibility, filesystem containment, template
checks, and other Python validators still require `ipe lint --strict` and an
end-to-end scenario.
