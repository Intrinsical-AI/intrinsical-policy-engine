# Write Your First Pack

Run these commands from an installed checkout, as described in the
[quickstart](../README.md#five-minute-quickstart). Work on a copy so the starter
and earlier outputs remain available:

```bash
author_run="$(mktemp -d "${TMPDIR:-/tmp}/ipe-author.XXXXXX")"
cp -R frameworks/starter "$author_run/pack"
cp -R demos/starter/basic "$author_run/input"
```

## 1. Follow the input-to-work path

The pack manifest owns version `0.3.0`, format `3`, the engine requirement
`>=3.0.0,<4.0.0`, and the paths below. Edit files in `$author_run/pack`:

| File | Purpose |
| --- | --- |
| `law/core/flags.yml` | Stable flag names. |
| `law/content/questions/00_scope.yml` | Questions that emit flags. |
| `law/core/rules.yml` | Positive derivations, stops, and classifiers. |
| `law/core/actions.yml` | Work, each action's `when`, and direct evidence paths. |
| `law/policy/evidence_map.yml` | Evidence inherited from action/article links. |
| `delivery/profiles/core_outputs.yml` | Which files to render and their trace links. |
| `render/artifacts/core/00_START_HERE.md.j2` | The first generated document. |
| `evidence/templates/starter/control-review.md` | Draft guidance for an evidence author. |

Start by changing the selected action's title and the first generated document.
Keep identifiers and references unchanged for this first iteration. Templates
use the single `answers` context; raw answers and user metadata are redacted by
default. See the [schema](POLICY_SCHEMA.md) before changing conditions or inputs.
Questions execute in declaration order and see only earlier answer emissions;
derived flags become available after the questionnaire. Give each new action its
own condition; there is no separate `packs` selection mechanism.

## 2. Validate and inspect the changed pack

```bash
uv run --frozen ipe lint --contracts "$author_run/pack" --strict
uv run --frozen ipe inspect --contracts "$author_run/pack" action STARTER-CONTROL-REVIEW
```

Lint checks contracts, references, template syntax, and template context names.
Inspect returns the compiled action as JSON. Correct any diagnostic before
continuing; [authoring errors](AUTHORING_ERRORS.md) maps failures to next steps.

## 3. Assess, then supply evidence for export

```bash
uv run --frozen ipe assess \
  --contracts "$author_run/pack" --answers "$author_run/input/answers.yml" \
  --out "$author_run/assessment" --strict
uv run --frozen ipe export \
  --contracts "$author_run/pack" --answers "$author_run/input/answers.yml" \
  --evidence-dir "$author_run/input/evidence" --check-ci --strict \
  --out "$author_run/export"
```

Read `assessment/_metadata/summary.json` and `assessment/_metadata/trace.json`:
the starter input selects `STARTER-CONTROL-REVIEW` after deriving
`starter.needs_review`. Review `export/00_START_HERE.md`, the backlog CSV, and the
copied evidence.

The copied demo evidence is a synthetic fixture that passes content checks.
For your own work, complete a separate evidence directory using the draft
template as guidance, then pass that directory with `--evidence-dir`. Every
required path needs ready content. Declaring `status: ready` or copying a draft
template does not satisfy the check. Evidence bytes are retained as supplied;
the raw-input option does not redact operational evidence.

## 4. Seal the evidence-bearing export

```bash
uv run --frozen ipe seal \
  --export-dir "$author_run/export" --output "$author_run/export.zip" \
  --strict --no-sign
```

This verifies the baseline and creates a complete unsigned archive. To sign,
configure GPG and an available signing key, then omit `--no-sign`. Sealing fails
on missing required evidence even without `--strict`; an assessment-only
directory is not an evidence-bearing export.
See the [signing runbook](CLI_REFERENCE.md#signing-and-recovery) for a disposable
local signing example and recovery guidance.

For another iteration, choose new output names such as `assessment-2`,
`export-2`, and `export-2.zip`. Reuse the copied pack and inputs, edit one change,
then lint and run again. Existing destinations, including empty directories,
are rejected.

## 5. Exercise a richer independent mock

The [EU AI Act process mock](../demos/process-review/README.md) uses the same
pack format for intake, review, and signoff. It is an educational technical
example with no legal fidelity and no connection to the complete framework
maintained elsewhere. Run it from the checkout root in a new directory:

```bash
mock_run="$(mktemp -d "${TMPDIR:-/tmp}/ipe-process-author.XXXXXX")"
cp -R demos/process-review/pack "$mock_run/pack"
uv run --frozen ipe lint --contracts "$mock_run/pack" --strict
uv run --frozen ipe export \
  --contracts "$mock_run/pack" \
  --answers demos/process-review/inputs/review.answers.json \
  --evidence-dir demos/process-review/evidence/complete \
  --check-ci --strict --out "$mock_run/export"
uv run --frozen ipe seal --export-dir "$mock_run/export" \
  --output "$mock_run/export.zip" --strict --no-sign
```

Expect `process.intake` and `process.review`, a review route, and synthetic
deadlines 2030-02-01 and 2030-02-06. Copying the pack tests its independence from
its repository path. Its [scenario corpus](../demos/process-review/expected/scenarios.json)
also covers stops, inactive questions, equivalent flags, missing evidence, and a
base-date override. Maintain expected conclusions independently of the engine
output; local scenarios do not establish a real pilot result or expert review.

## Target configuration

Filesystem is the default target. The checked examples in
[`demos/starter/configs`](../demos/starter/configs) cover filesystem, Asana, Jira,
Linear, and all four together. To generate all offline outputs in a new path:

```bash
uv run --frozen ipe export \
  --contracts "$author_run/pack" --answers "$author_run/input/answers.yml" \
  --evidence-dir "$author_run/input/evidence" --check-ci --strict \
  --config demos/starter/configs/offline-all.json \
  --target filesystem asana jira linear --out "$author_run/export-all"
```

Read `exports/<target>/requests.ndjson` and `index.json`; Asana also emits
`exports/asana/import.csv`. These are request descriptions, and the CLI sends
no provider requests. The example hosts and IDs are synthetic. See the
[CLI reference](CLI_REFERENCE.md#target-configuration) for every target field,
profile selection, paths, and credential handling.
