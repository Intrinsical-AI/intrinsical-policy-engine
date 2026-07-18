# Intrinsical Policy Engine™

[![CI](https://github.com/Intrinsical-AI/intrinsical-policy-engine/actions/workflows/ci.yml/badge.svg)](https://github.com/Intrinsical-AI/intrinsical-policy-engine/actions/workflows/ci.yml)
[![License: MPL-2.0](https://img.shields.io/badge/License-MPL_2.0-blue.svg)](LICENSE)

Intrinsical Policy Engine is a framework-neutral policy/rules engine suitable
for auditable assessment workflows. It turns structured answers plus a
versioned framework pack into a reproducible assessment plan, export package,
trace, and seal metadata.

This public repository includes a small starter pack for educational authoring.
It does not include any regulatory pack.

Organizations, consultancies, and internal platform teams may use the public
core under MPL-2.0 to evaluate, fork, and build their own reviewed framework
packs. Any production pack should be owned, reviewed, tested, and approved by
the adopting organization.

## Hard Limits

Intrinsical Policy Engine is not legal advice, not an official or endorsed
compliance framework, and not a certification tool. It does not guarantee
compliance with any law, standard, contractual requirement, or internal policy.

Integrity and sealing features help detect package drift. They prove package
integrity only; they do not prove legal truth, operational correctness, or
regulatory acceptance. Use this software at your own risk under the MPL-2.0
no-warranty terms.

## Development Status

This branch declares `3.0.0a1` while the 3.0 packaging and embedding contracts
are being validated. It is an unreleased, local alpha: the version in this
checkout or in a wheel built from it is not evidence of a published release or
of remote CI status. See [CHANGELOG.md](CHANGELOG.md) for released 2.x versions
and the current unreleased changes.

## Quickstart

```bash
uv sync --all-extras --dev

uv run ipe lint --contracts frameworks/starter --strict

IPE_SKIP_GPG_SIGNING=1 uv run ipe export \
  --contracts frameworks/starter \
  --answers demos/starter/basic/answers.json \
  --out out/starter \
  --strict \
  --strict-templates

uv run ipe seal --export-dir out/starter --no-sign
```

The starter export should produce metadata under `out/starter/_metadata/` and a
sealed package manifest/checksum set. The quickstart opts out of GPG signing
explicitly; omit `--no-sign` for signed delivery and ensure a secret key is
available.

### Embedding API (3.0 alpha)

Products embedding the engine should use the supported facade rather than
importing application or domain modules directly:

```python
from pathlib import Path

from intrinsical_policy_engine.api import (
    AssessmentRequest,
    Engine,
    ExecutionPolicy,
)

result = Engine().assess(
    AssessmentRequest(
        pack=Path("frameworks/starter"),
        answers={"uses_automated_decisions": True},
        policy=ExecutionPolicy(strict=True),
    )
)
if not result.success:
    raise RuntimeError([diagnostic.code for diagnostic in result.diagnostics])
```

The 3.0 alpha facade covers pack validation, assessment, export, sealing, typed
diagnostics, and composable gate reports. See
[docs/MIGRATION_3.md](docs/MIGRATION_3.md) for the compatibility and packaging
contract.

An embedding product may pass an optional `ProductIdentity` on
`ExportRequest`; this records the product name and version in export provenance
without changing the engine or pack identity. Raw answers are sensitive and
are not persisted by default. Persisting them requires the explicit
`include_raw_answers=True` API setting or CLI `--include-raw-answers` flag.

`release=True` is also an explicit policy boundary: a release export requires
`ExecutionPolicy(strict=True)` and rejects incomplete-coverage and unsigned
output bypasses before creating its output directory.

The 3.0 wheel includes a PEP 561 `py.typed` marker, so type checkers can consume
the annotations exposed by `intrinsical_policy_engine` from an installed wheel.

## What Is Included

- A Python engine for loading framework packs, evaluating rules, building
  assessment plans, exporting artifacts, tracing, and sealing outputs.
- A neutral starter pack that demonstrates the minimum authoring path.
- Public documentation for writing, validating, exporting, and testing a small
  pack.
- CI checks for linting, typing, starter-pack execution, tests, and public
  release leak scanning.

## What Is Not Included

See [BOUNDARIES.md](BOUNDARIES.md). In short: no regulatory packs, no policy
calibrations from private work, no commercial evidence templates, no sector
demos, no generated real-world outputs, and no private repository history.

## Documentation

- [CHANGELOG.md](CHANGELOG.md)
- [PROVENANCE.md](PROVENANCE.md)
- [BOUNDARIES.md](BOUNDARIES.md)
- [docs/WRITE_YOUR_FIRST_PACK.md](docs/WRITE_YOUR_FIRST_PACK.md)
- [docs/MINIMUM_AUTHORING_PATH.md](docs/MINIMUM_AUTHORING_PATH.md)
- [docs/AUTHORING_ERRORS.md](docs/AUTHORING_ERRORS.md)
- [docs/POLICY_SCHEMA.md](docs/POLICY_SCHEMA.md)
- [docs/SECURITY_AND_LIMITATIONS.md](docs/SECURITY_AND_LIMITATIONS.md)
- [docs/ENVIRONMENT_COMPATIBILITY.md](docs/ENVIRONMENT_COMPATIBILITY.md)
- [docs/MIGRATION_3.md](docs/MIGRATION_3.md) — 3.0 alpha API and packaging contract

## License

The public core is licensed under MPL-2.0. See [LICENSE](LICENSE).

## Marks

Intrinsical Policy Engine™ and Intrinsical-AI™ are names used by Pablo P.C. for this project and related software/services. Use of these names must not imply endorsement, sponsorship, certification, or commercial affiliation with Pablo P.C.
