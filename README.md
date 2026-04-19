# Intrinsical Policy Engine™

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

uv run ipe seal --export-dir out/starter
```

The starter export should produce metadata under `out/starter/_metadata/` and a
sealed package manifest/checksum set.

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

- [PROVENANCE.md](PROVENANCE.md)
- [BOUNDARIES.md](BOUNDARIES.md)
- [docs/WRITE_YOUR_FIRST_PACK.md](docs/WRITE_YOUR_FIRST_PACK.md)
- [docs/MINIMUM_AUTHORING_PATH.md](docs/MINIMUM_AUTHORING_PATH.md)
- [docs/AUTHORING_ERRORS.md](docs/AUTHORING_ERRORS.md)
- [docs/POLICY_SCHEMA.md](docs/POLICY_SCHEMA.md)
- [docs/SECURITY_AND_LIMITATIONS.md](docs/SECURITY_AND_LIMITATIONS.md)

## License

The public core is licensed under MPL-2.0. See [LICENSE](LICENSE).

## Marks

Intrinsical Policy Engine™ and Intrinsical-AI™ are names used by Pablo P.C. for this project and related software/services. Use of these names must not imply endorsement, sponsorship, certification, or commercial affiliation with Pablo P.C.