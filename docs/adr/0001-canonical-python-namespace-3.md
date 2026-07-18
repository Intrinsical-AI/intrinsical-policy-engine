# ADR-0001: Canonical Python namespace for 3.0

- Status: Accepted
- Date: 2026-07-18
- Target release: 3.0.0
- Supersedes: the 2.x top-level `src` import package

## Context

The 2.0.1 distribution installs the repository's source directory itself as a
Python package. Its console entry point is `src.app.cli.main:main`, and all
runtime layers import one another through `src.*`.

This shape has several concrete problems:

- `src` is a generic package name and can collide with unrelated projects.
- The checkout is not a conventional Python “src layout”; the directory named
  `src` is both the source root and the import package.
- Downstream products cannot safely depend on the public wheel while also
  shipping another copy of the same package tree.
- Runtime modules import repository-only `scripts.*` modules and derive the
  repository root from fixed `Path(__file__).parents[n]` offsets. Those
  assumptions are not valid installation contracts.
- There is no small, declared Python facade for downstream integrations.

At the 2.0.1 tag, the migration surface consists of 172 Python modules and
32,099 lines under `src`. An AST inventory finds 444 `src.*` import statements
across 104 source, test, and script files; 436 statements are in 99 runtime
modules. The runtime also contains nine `sys.path`
mutations, twelve fixed-parent path lookups, and eight `LEXOPS_*` compatibility
fallbacks in six modules.

## Decision

### Distribution and package layout

The distribution name remains `intrinsical-policy-engine`. Its sole runtime
top-level Python package becomes `intrinsical_policy_engine` in a true source
layout:

```text
src/
└── intrinsical_policy_engine/
    ├── __init__.py
    ├── __main__.py
    ├── api/
    ├── adapters/
    ├── app/
    ├── common/
    └── domain/
```

Package discovery will search from `src`, include only
`intrinsical_policy_engine*`, and use regular packages with explicit
`__init__.py` files. The wheel must not install top-level packages named `src`
or `scripts`.

Repository scripts, tests, demos, framework examples, caches, and local
sentinels are not wheel contents. Runtime functionality needed by the wheel
must live under `intrinsical_policy_engine`; a repository script may be a thin
wrapper around that runtime API, never the implementation imported by it.

### Public entry points

The stable distribution and entry-point contract is:

| Surface | 2.x | 3.0 |
| --- | --- | --- |
| Distribution | `intrinsical-policy-engine` | unchanged |
| Console command | `ipe` | unchanged |
| Module command | `python -m src.app.cli` | `python -m intrinsical_policy_engine` |
| Import root | `src` | `intrinsical_policy_engine` |

Both 3.0 command forms invoke the same `main` function and report the same
version and exit codes.

The supported Python integration surface will be exposed from
`intrinsical_policy_engine.api`. The initial facade will provide the engine
entry point, typed assessment/export/seal request and result objects, effective
execution policy, gate report, and contract diagnostics. Other subpackages are
implementation details unless explicitly documented as public.

### Compatibility

3.0 will not install a `src` compatibility shim. The name is too broad, a shim
would retain the collision this ADR removes, and importing it would also mask
incomplete migrations. Import changes are therefore an intentional major
version break documented in `MIGRATION_3.md`.

The `ipe` executable is preserved. JSON/YAML pack and artifact compatibility is
versioned independently and is not changed merely by moving Python modules.
Python pickles and other payloads containing fully-qualified 2.x class names
are not supported migration formats.

### Configuration boundary

Core and adapter modules will not read environment variables directly. The CLI
or another product composition root translates supported `IPE_*` inputs into a
typed execution/configuration object.

All functional `LEXOPS_*` fallbacks are removed in 3.0. If a legacy variable
is detected at the public CLI boundary, startup fails with a migration hint; its
value is never used. Security-sensitive bypasses cannot qualify a release-mode
run as publishable.

### Downstream products

A downstream product may depend on the public distribution only if it ships a
different top-level package and does not vendor or overwrite
`intrinsical_policy_engine`. Product-specific CLI, configuration, branding, and
packs remain outside the public core. This dependency direction removes manual
core drift and allows both distributions to coexist in one environment.

Adopting that dependency model in another repository requires its own migration
decision; this ADR only establishes the public wheel contract that makes it
possible.

## Consequences

### Positive

- Standard Python packaging and an unambiguous import name.
- A wheel that works independently of a repository checkout.
- A reviewable public API boundary for downstream products.
- No duplicate top-level core packages when the engine is used as a dependency.
- Namespace and package-content regressions can be enforced in CI.

### Costs and risks

- Every absolute `src.*` import, lazy import, doctest, and test reference must be
  migrated atomically with package discovery and the console entry point.
- Fixed-parent path calculations change depth after the move; incrementing
  their indexes would preserve a broken installed-wheel assumption, so they
  must be replaced with explicit inputs or package-resource APIs.
- The 2.0.1 public suite is small, so characterization and installed-wheel
  smokes are mandatory before the move.
- Downstream users of undocumented deep imports must update them.

## Alternatives rejected

- **Keep `src` as the package:** preserves collisions and prevents a clean
  dependency relationship.
- **Add a permanent `src` shim:** hides incomplete work and continues claiming
  a generic top-level name.
- **Use a flat repository-root package:** better than 2.x but provides weaker
  protection against accidentally importing checkout files during tests.
- **Use an implicit namespace package:** unnecessary for a single distribution
  and makes package-content mistakes harder to detect.
- **Keep runtime code in `scripts`:** makes installed behavior depend on
  repository tooling and complicates wheel ownership.

## Enforcement

The characterization tests in `tests/unit/test_python_package_contract.py`
enforce the source-tree namespace contract on every run. Wheel-content gates
use `IPE_3_0_WHEEL` and must point at the exact artifact being promoted.
