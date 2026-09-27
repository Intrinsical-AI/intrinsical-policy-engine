# Migrating to Intrinsical Policy Engine 3.0

This guide describes the Python packaging and configuration break from 2.x to
the 3.0 alpha line. The contracts below are implemented on
`feat/3.0.0-core`; they are not available in 2.0.1 and remain subject to
pre-release compatibility until 3.0.0 is published.

The current checkout declares `3.0.0a1`. That is an unreleased local alpha,
not a claim that an artifact has been published or that remote CI has passed.
Use the wheel verification described below against the exact artifact under
review rather than inferring readiness from the source version alone.

## What remains stable

- The distribution is still installed as `intrinsical-policy-engine`.
- The console command remains `ipe`.
- Framework packs remain external inputs to the engine.
- Moving Python modules does not by itself change pack or generated-artifact
  schemas. Those schemas keep their own version gates.

## Required import changes

The 2.x package name `src` is removed. Replace its prefix with the canonical
package name:

| 2.x import | 3.0 import |
| --- | --- |
| `src.domain.*` | `intrinsical_policy_engine.domain.*` |
| `src.app.*` | `intrinsical_policy_engine.app.*` |
| `src.adapters.*` | `intrinsical_policy_engine.adapters.*` |
| `src.common.*` | `intrinsical_policy_engine.common.*` |

For new integrations, prefer the supported facade instead of depending on an
implementation layer:

```python
# 3.0 supported integration surface.
from intrinsical_policy_engine.api import Engine, ExecutionPolicy
```

The facade owns typed pack-validation, assessment, export, and seal
requests/results plus gate and diagnostics objects. Deep modules are not stable
unless the public API reference explicitly lists them.

### Supported embedding flow

```python
from pathlib import Path

from intrinsical_policy_engine.api import (
    Engine,
    ExecutionPolicy,
    ExportRequest,
    GateCheck,
    GateStatus,
    ProductIdentity,
    evaluate_gate,
)

engine = Engine()
result = engine.export(
    ExportRequest(
        pack=Path("frameworks/acme-policy"),
        answers={"subject.enabled": True},
        output_dir=Path("out/acme-policy"),
        policy=ExecutionPolicy(strict=True, skip_gpg_signing=True),
        product=ProductIdentity(name="acme-policy-console", version="1.4.0"),
    )
)

# A product may tighten the engine decision, but cannot override a blocker.
report = evaluate_gate(
    GateCheck.from_decision("engine.export", result.gate),
    GateCheck("product.approval", GateStatus.PASSED, source="product"),
)
if not report.allowed:
    raise RuntimeError([diagnostic.code for diagnostic in report.diagnostics])
```

`EngineConfig` accepts a `PackProvider` and a default `ExecutionPolicy`.
Operation-specific policy is explicit on its request and takes precedence over
that default. Results do not expose the loaded contract bundle or internal
domain models.

`ExecutionPolicy` currently controls strict contract/template handling, trace
detail, export mode, tolerated question errors, the explicit incomplete-
coverage exception, demo marking, and unsigned CI/dev output. A release export
rejects incomplete-coverage or signing bypasses before creating its output
directory.

`ProductIdentity` is optional and applies only to an export. When supplied, its
non-empty name and version are written as product provenance alongside the
independent engine, pack, and artifact-schema versions. It does not rename the
engine, change pack compatibility, or make a product gate override an engine
blocker.

### Pack compatibility

Every 3.x pack manifest must declare `compatible_engine_versions` using one or
more PEP 440 specifier sets. A pack that supports this alpha should use an
explicit pre-release floor:

```yaml
version: 3.0.0a1
compatible_engine_versions:
  - ">=3.0.0a1,<4.0.0"
```

`>=3.0.0,<4.0.0` intentionally does not accept `3.0.0a1`. Promote the floor
to `>=3.0.0,<4.0.0` when the pack and engine move to the stable release.
`license_file`, when present, must be an existing pack-relative file and must
not escape the pack through path traversal or symlinks.

There is no 3.0 `src` alias and no automatic import rewrite. A successful
`import src` in an application environment indicates another package or a
checkout-path leak, not an IPE compatibility feature.

## CLI migration

The installed executable is unchanged:

```bash
ipe --version
ipe lint --contracts frameworks/starter --strict
```

Module execution changes:

```bash
# 2.x — removed in 3.0
python -m src.app.cli --version

# 3.0
python -m intrinsical_policy_engine --version
```

Both supported 3.0 command forms use the same parser and return the same exit
codes.

## Environment variables

The public `IPE_*` names are parsed only at the CLI/configuration boundary and
converted into typed configuration. Core and adapter modules do not call
`os.getenv`.

The following 2.x fallbacks are removed:

| Public input | Removed fallback | 3.0 behavior |
| --- | --- | --- |
| `IPE_ENV` | `LEXOPS_ENV` | Public input maps to the execution profile. |
| `IPE_STRICT_CONTRACTS` | `LEXOPS_STRICT_CONTRACTS` | Public input maps to contract-load policy. |
| `IPE_TOLERATE_QUESTIONS_ERRORS` | `LEXOPS_TOLERATE_QUESTIONS_ERRORS` | Public input maps to an explicit loader option. |
| `IPE_ALLOW_INCOMPLETE_COVERAGE` | `LEXOPS_ALLOW_INCOMPLETE_COVERAGE` | Allowed only where the effective non-release policy permits it. |
| `IPE_DEMO_MODE` | `LEXOPS_DEMO_MODE` | Public input maps to the selected product/profile configuration. |
| `IPE_DEV_MODE` | `LEXOPS_DEV_MODE` | Controls only the public maintainer CLI surface. |
| `IPE_OUT_DIR` | `LEXOPS_OUT_DIR` | Public input maps to the validated output path. |
| `IPE_SKIP_GPG_SIGNING` | `LEXOPS_SKIP_GPG_SIGNING` | Produces explicitly unsigned CI/dev output and cannot satisfy release publication policy. |

If the public CLI sees one of the removed names, it reports which `IPE_*` name
or typed setting replaces it and exits without consuming the legacy value.

## Export privacy and release policy

Raw answers are not persisted by default. In particular, a normal export does
not write `_metadata/wizard_answers.json`, and the default trace records a
one-way answers hash rather than the raw answer payload. Persisting the payload
requires an explicit opt-in:

```python
ExportRequest(
    pack=Path("frameworks/acme-policy"),
    answers=answers,
    output_dir=Path("out/acme-policy"),
    include_raw_answers=True,
)
```

The CLI equivalent is `ipe export --include-raw-answers ...`. Treat the
resulting metadata file as sensitive. This default limits raw-answer
persistence; it is not a promise that derived assessment or rendered artifact
content is anonymous.

A release export is stricter than an ordinary export. `release=True` requires
an effective `ExecutionPolicy(strict=True)`. A non-strict release request is
blocked with `RELEASE_REQUIRES_STRICT_POLICY` before its output directory is
created. Release mode also rejects `allow_incomplete_coverage=True` and
`skip_gpg_signing=True`; callers must not use either bypass as release
evidence.

## Packaging contract

The 3.0 wheel contains one runtime top-level package:

```text
intrinsical_policy_engine/
```

It does not contain:

- a `src` or `scripts` top-level package;
- tests, demos, repository framework examples, or local caches;
- `.codex` or other workspace sentinels.

The canonical package contains `py.typed` and declares it as package data.
Installed consumers can therefore type-check the public facade under PEP 561;
verification must inspect the built wheel rather than relying only on the
marker present in the source tree.

Runtime validation currently implemented in a repository script must move into
the canonical package. Repository scripts may remain only as thin wrappers.
Commands that refer to script modules not shipped by the public repository must
either receive a supported implementation or be removed before 3.0.

Code must not locate data by counting parents from `__file__`. Callers pass
external pack/output paths explicitly; packaged resources use
`importlib.resources`.

## Downstream product migration

A product that consumes the public core should:

1. Depend on a compatible `intrinsical-policy-engine` 3.x version.
2. Remove any vendored copy of the `intrinsical_policy_engine` tree.
3. Ship product code under a distinct top-level namespace.
4. Translate product environment/configuration into the public typed API.
5. Keep private packs and product presentation outside the core wheel.
6. Run its integration suite against the exact locked public-core version.

Two distributions must not both install files into
`intrinsical_policy_engine`. If a downstream repository retains a duplicate
copy, the distributions remain mutually exclusive and it is not using the
public engine as a dependency.

## Unsupported compatibility

- Python pickles or other payloads containing `src.*` qualified class names are
  not migrated.
- Monkeypatch targets and plugin strings containing `src.*` must be updated.
- Undocumented deep imports receive no shim.
- Importing directly from a repository checkout is not an installation test.

JSON/YAML inputs and exported artifacts are governed by their declared schema
versions, not by Python module names.

## Maintainer rollout

1. Freeze the 2.0.1 module, CLI-help, export, and wheel manifests.
2. Move runtime dependencies out of `scripts` and remove repository path hacks.
3. Move the package and rewrite all imports in one atomic batch.
4. Update package discovery, entry points, lint/type/coverage paths, and lockfile.
5. Add the public facade and central configuration reader.
6. Keep the source-tree namespace contracts enabled in ordinary CI runs.
7. Build the wheel and run its contract with `IPE_3_0_WHEEL=/path/to/wheel`.
8. Run lint/export/seal from an isolated environment using an external starter
   pack path.
9. Publish a release candidate only when the old namespace is absent from the
   wheel and all documentation examples.

## Verification commands

The source-tree namespace and import contracts are always on:

```bash
pytest tests/unit/test_python_package_contract.py
```

After building a wheel:

```bash
IPE_3_0_WHEEL=dist/intrinsical_policy_engine-3.0.0a1-py3-none-any.whl \
  pytest tests/unit/test_python_package_contract.py
```

The wheel-specific assertions skip only when `IPE_3_0_WHEEL` is absent. Release
verification must set it to the exact artifact being promoted.

The wheel contract checks the distribution name and 3.0 version, the `ipe`
entry point, the single `intrinsical_policy_engine` top-level runtime package,
the absence of repository-only trees, and the packaged `py.typed` marker. These
commands describe local artifact checks; they do not by themselves publish the
alpha or report remote CI as green.
