# EU AI Act process mock

This is an independent educational pack for practicing technical authoring. Its
three generic tasks are intake, review and signoff. It has **no legal fidelity**:
it does not implement legislation, map legal requirements or certify a process.
The complete framework is defined separately and is not reproduced here. This
example uses the public starter structure; no private framework was copied.

The pack is ordinary external input to the engine. It lives entirely below
`demos/process-review/pack`, declares its own identifiers, questions, rules,
templates and evidence paths, and can be copied to a different directory.
No engine changes or imports from this demo are required to run it.

## Run a complete example

From the checkout root, with the environment synced:

```bash
mock_run="$(mktemp -d "${TMPDIR:-/tmp}/ipe-process-mock.XXXXXX")"
uv run --frozen ipe lint --contracts demos/process-review/pack --strict
uv run --frozen ipe assess \
  --contracts demos/process-review/pack \
  --answers demos/process-review/inputs/review.answers.json \
  --out "$mock_run/assessment"
uv run --frozen ipe export \
  --contracts demos/process-review/pack \
  --answers demos/process-review/inputs/review.answers.json \
  --evidence-dir demos/process-review/evidence/complete \
  --check-ci --strict --out "$mock_run/export"
uv run --frozen ipe seal \
  --export-dir "$mock_run/export" \
  --output "$mock_run/export.zip" --strict --no-sign
printf 'Results: %s\n' "$mock_run"
```

Expect the `process.intake` and `process.review` tasks, route `process.review`,
and the fictional dates 2030-02-01 and 2030-02-06. Read `export/00_START_HERE.md`,
`export/README.md`, the copied records in `export/evidence/process`, and the
assessment summary and trace in `export/_metadata`.

## What this pack demonstrates

| Mechanism | Small example |
| --- | --- |
| Ordered questions | Submission activates review and coordinator questions; review plus an assigned coordinator activates signoff. |
| Derived flags | Direct answer flags derive `process.intake_required`, `process.review_required`, and `process.approval_required` after the questionnaire. |
| Selection | Each task declares an `action.when` condition on its corresponding work flag. |
| Stop | A declared missing coordinator produces `process.blocked`, selects no tasks, and fails `--check-ci`. |
| Routing | The first matching route is hold, signoff, review, or intake; unmatched input uses idle. |
| Dates | Offsets of two, seven and ten days use the fixed example base 2030-01-30; `--base-date` can override it. |
| Evidence | Every selected task requires its own record; templates are unfinished guides and never substitute for supplied evidence. |

The questionnaire checks only flags emitted by earlier answers. Its signoff
question deliberately uses `process.review_requested` and
`process.owner_assigned`, rather than flags derived after the questions finish.
The engine's tier field labels the stage here; these names are not legal risk
categories. A signoff task requests a record and does not mean approval occurred.

## Files to author

| Location | Purpose |
| --- | --- |
| `pack/manifest.yml` | Pack format, version and explicit file references. |
| `pack/contracts/` | Flags, questions, tasks, topics, rules, evidence and fictional dates. |
| `pack/runtime/` | Declared stage/outcome vocabularies, routing and presentation. |
| `pack/delivery/profiles/` | Output selection and trace references. |
| `pack/render/` | Local templates and backlog settings. |
| `pack/evidence/templates/process/` | Unfinished guides for a record author. |
| `inputs/` | Explicit answers and equivalent flags envelopes. |
| `evidence/complete/` | Three completed synthetic records used only as fixtures. |
| `evidence/intake-only/` | Deliberately incomplete fixture for a negative scenario. |
| `expected/` | Independently authored conclusions and scenario outcomes. |

To adapt the mock, copy the pack, change its version, author its questions and
selection conditions, and replace the synthetic records with reviewed evidence.
Keep conditions consistent with question order. Review expected conclusions
before changing the corpus; do not regenerate goldens from actual engine output.

## Expected scenarios

`expected/scenarios.json` records ten cases. Golden files compare semantic
conclusions, evidence maps, derivations, stops and dates; they omit timestamps,
source fingerprints and other execution details.

| Scenario | Expected result |
| --- | --- |
| `review-answers` | Intake and review; same semantic fingerprint as equivalent flags. |
| `review-flags` | The same conclusions from a flags envelope. |
| `approval` | Intake, review and signoff; route `process.signoff`. |
| `intake-only` | Intake without review; inactive signoff answer omitted. |
| `idle` | No submitted item, no selected task or calendar. |
| `owner-missing` | Assessment records the stop; export with `--check-ci` fails. |
| `inactive-question` | An answer to the inactive review question is rejected. |
| `invalid-option` | An undeclared answer option is rejected. |
| `missing-evidence` | Assessment succeeds without a CI check; checked export rejects the missing review record. |
| `date-override` | Explicit base 2030-02-27 yields intake on 2030-03-01 and review on 2030-03-06. |

Run the corpus through the CLI smoke test:

```bash
uv run --frozen pytest -q tests/smoke/test_process_review_mock.py
```

Successful scenarios also export and seal a new snapshot. Rejected scenarios
must leave no output at the rejected destination. A separate check copies the
pack to a different directory before loading it.

## Acceptance limits

These checks demonstrate local execution, content checks and file integrity.
They do not establish legal correctness, production suitability, an external
pilot result, acceptance by a subject expert, or an integration with a provider.
The synthetic evidence is retained exactly; review real records before sharing
an export. An unsigned archive identifies no signer, and signing would not
approve its contents.
