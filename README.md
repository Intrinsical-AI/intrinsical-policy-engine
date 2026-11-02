# Intrinsical Policy Engine™

[![CI](https://github.com/Intrinsical-AI/intrinsical-policy-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/Intrinsical-AI/intrinsical-policy-engine/actions/workflows/ci.yml)
[![License: MPL-2.0](https://img.shields.io/badge/License-MPL_2.0-blue.svg)](LICENSE)

Intrinsical Policy Engine evaluates a versioned framework pack against structured
answers or explicit flags. Its CLI validates packs, creates assessment plans,
exports artifacts, explains rules, and seals snapshots.

The source checkout includes an educational starter and an independent
[EU AI Act process mock](demos/process-review/README.md). The mock practices
intake, review, and signoff with synthetic records; the complete framework lives
elsewhere and is neither imported nor reproduced here. Production packs need
their own policy owner, review, tests, and acceptance criteria. The engine and
its integrity checks do not establish legal correctness or certification.

## Five-minute quickstart

Python 3.11+ and [uv](https://docs.astral.sh/uv/) are required for this checkout.
The supported execution matrix is Linux with Python 3.11 and 3.12, as configured
in CI; macOS and Windows are not accepted platforms. Linux output publication
requires `renameat2` with exclusive rename support. Run from the checkout root.
Each run keeps its results in a fresh temporary directory:

```bash
uv sync --locked
example_run="$(mktemp -d "${TMPDIR:-/tmp}/ipe-starter.XXXXXX")"
uv run --frozen ipe --version
uv run --frozen ipe lint --contracts frameworks/starter --strict
uv run --frozen ipe inspect --contracts frameworks/starter
uv run --frozen ipe assess \
  --contracts frameworks/starter \
  --answers demos/starter/basic/answers.json \
  --out "$example_run/assessment"
uv run --frozen ipe export \
  --contracts frameworks/starter \
  --answers demos/starter/basic/answers.json \
  --evidence-dir demos/starter/basic/evidence \
  --check-ci --strict \
  --out "$example_run/export"
uv run --frozen ipe seal \
  --export-dir "$example_run/export" \
  --output "$example_run/export.zip" --strict --no-sign
printf 'Results: %s\n' "$example_run"
```

Expect `ipe 3.0.0`, a valid pack, and the selected action
`STARTER-CONTROL-REVIEW`. Open these files under the printed results directory:

- `assessment/_metadata/summary.json`: selected flags, actions, outcomes, and dates.
- `assessment/_metadata/trace.json`: input hash, derivations, and rule decisions.
- `export/00_START_HERE.md` and `export/README.md`: the generated work and next steps.
- `export/evidence/starter/control-review.md`: the supplied evidence copy.
- `export/CHECKSUMS.sha256` and `export.zip`: the verified snapshot and unsigned archive.

Run the block again for a fresh directory; earlier results remain available.
Assessment and export paths must not exist yet, including empty directories.
An archive path must also be new and outside its source snapshot.

The demo evidence is a synthetic test fixture. `--check-ci` requires explicitly
supplied evidence for the selected requirements, validates it, and copies it
into the snapshot. Every required evidence path must resolve to ready content.
Templates supplied by a pack are authoring material; they do not establish that
the work was performed.

`assess` writes a plan and trace. It does not copy an evidence snapshot, so seal
the evidence-bearing `export` directory. Sealing signs with GPG by default and
requires GPG plus an available signing key. The example chooses `--no-sign` for
an unsigned archive. The starter has no deadlines and produces no calendar file.

To exercise ordered questions, stops, routing, and relative dates, use the
[process mock quickstart](demos/process-review/README.md#run-a-complete-example).
Its scenarios are local technical fixtures, with no legal fidelity or real pilot
acceptance.

## Install a wheel or source distribution

`uv sync --locked` installs this checkout and its locked development tools.
To exercise a built artifact in a separate environment, run from the checkout:

```bash
artifact_run="$(mktemp -d "${TMPDIR:-/tmp}/ipe-installed.XXXXXX")"
uv build --out-dir "$artifact_run/dist"
uv venv --python 3.12 "$artifact_run/venv"
uv pip install --python "$artifact_run/venv/bin/python" \
  "$artifact_run/dist/intrinsical_policy_engine-3.0.0-py3-none-any.whl"
uv pip check --python "$artifact_run/venv/bin/python"
"$artifact_run/venv/bin/ipe" --version
"$artifact_run/venv/bin/python" scripts/check_installed_artifact.py \
  --ipe "$artifact_run/venv/bin/ipe" --fixtures "$PWD" \
  --workdir "$artifact_run/acceptance"
```

Use `intrinsical_policy_engine-3.0.0.tar.gz` instead of the wheel to install the
source distribution. Install each artifact into a fresh environment. Artifact
installation resolves the declared runtime requirements; CI also checks them
against a hashed export of `uv.lock`. Neither artifact bundles packs, demos,
documentation directories, or development scripts. Keep a separate fixture
checkout for `--fixtures` and the acceptance script; supply your own packs and
evidence as external files. Run the installed CLI directly as shown above;
`uv run` inside a checkout selects its project environment.

## CLI and input contract

The CLI has five commands: `lint`, `assess`, `export`, `seal`, and `inspect`.
Use `ipe <command> --help` for their complete arguments. `lint` validates pack
contracts, templates, and references. `--strict` makes validation warnings fatal;
validation errors always fail.

An input JSON or YAML document contains exactly one of `answers`, a mapping from
question IDs to values, or `flags`, an explicit list of declared flag names.
Optional identity and approval metadata uses the typed `system`, `provider`,
`owner`, `declared_by`, `approvals`, and `meta` fields; their values must be JSON
data with finite numbers. Unknown top-level fields and invalid values are errors.
Rendering exposes the question mapping as `answers`.

Answers and user metadata are redacted before export destinations receive them.
Use `--include-raw-input` to retain their unredacted values explicitly.
Review templates and generated artifacts before sharing them.

Filesystem export includes the assessment trace, selected evidence, and calendar
artifacts. Asana, Jira, and Linear targets generate offline request records under
`exports/<target>/`, with explicit `--target` and typed `--config` settings.
Sending those requests and validating provider acceptance are separate steps.
See [target configuration](docs/WRITE_YOUR_FIRST_PACK.md#target-configuration).
The [CLI reference](docs/CLI_REFERENCE.md) lists arguments, defaults, paths,
complete target settings, and operational logging.

## Packs and development

The engine version is defined in `pyproject.toml` and read from installed package
metadata. Pack format 3 uses `manifest.yml`, including its pack version and
`requires_engine` constraint. Rule conditions use the structured YAML DSL
[documented here](docs/POLICY_SCHEMA.md).

```bash
uv lock --check
uv run --frozen python scripts/export_schemas.py --check --out-dir docs/schemas
uv run --frozen ruff check src tests scripts
uv run --frozen ruff format --check src tests scripts
uv run --frozen mypy src scripts
uv run --frozen pytest tests/unit tests/smoke
uv run --frozen pre-commit run --all-files
uv build
uv run --frozen python scripts/check_public_release.py \
  --distribution dist/intrinsical_policy_engine-3.0.0-py3-none-any.whl \
  --distribution dist/intrinsical_policy_engine-3.0.0.tar.gz
```

CI checks Python 3.11 and 3.12, builds both wheel and source distribution, and
runs their installed assessment/export/seal flows outside the source checkout.
The installed acceptance script checks all five commands, their help and logs,
rejected inputs and destinations, explicit evidence, redaction and raw opt-in,
offline API payloads, checksum drift, and complete unsigned ZIP contents. It
saves command receipts in its work directory's `acceptance.json`. These are
configured checks; a release needs receipts from its actual revision and
artifacts. Local success does not establish remote CI, human pilot acceptance,
provider delivery, or publication. GPG signing requires its own acceptance.

Measure the deterministic synthetic workload separately from correctness checks:

```bash
benchmark_run="$(mktemp -d "${TMPDIR:-/tmp}/ipe-benchmark.XXXXXX")"
uv run --frozen python scripts/benchmark_engine.py \
  --output "$benchmark_run/benchmark.json" --sizes 10 100 1000 --repeats 3
```

The default report records observed timings and provenance without a performance
budget. Add `--baseline PATH --max-slowdown FRACTION` together to compare median
elapsed times for assessment and evidence collection/CI. For example, `0.25`
permits at most 1.25 times each baseline median. Comparison requires identical
benchmark-script SHA-256, workload hashes, measurement method, sizes, repetitions,
Python, compiler, platform, machine, and dependencies; the engine source and
version may differ. CPU time and allocation peaks remain observations. An
incomparable baseline exits 2 without a report; a comparable regression writes
its comparison receipt and exits 1. This operator-chosen tolerance establishes no
production capacity guarantee. See the
[release runbook](docs/CLI_REFERENCE.md#release-runbook) for artifact acceptance
and the [Python API](docs/POLICY_SCHEMA.md#python-api) for the package namespace.
`scripts/check_conformance.py` separately runs the mock's expected scenarios
against an explicit installed CLI and writes `conformance.json`; its invocation
is included in the release runbook.

## Documentation

- [Write your first pack](docs/WRITE_YOUR_FIRST_PACK.md)
- [CLI reference](docs/CLI_REFERENCE.md)
- [Minimum authoring path](docs/MINIMUM_AUTHORING_PATH.md)
- [Authoring errors](docs/AUTHORING_ERRORS.md)
- [Policy and input schema](docs/POLICY_SCHEMA.md)
- [Security and limitations](docs/SECURITY_AND_LIMITATIONS.md)
- [Public boundaries](BOUNDARIES.md)
- [Security policy](SECURITY.md)
- [Changelog](CHANGELOG.md)

## License and marks

The public core is licensed under [MPL-2.0](LICENSE).

Intrinsical Policy Engine™ and Intrinsical-AI™ are names used by Pablo P.C.
for this project and related software/services. Their use must not imply
endorsement, sponsorship, certification, or commercial affiliation.
