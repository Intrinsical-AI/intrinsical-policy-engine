# Changelog

All notable changes to the public Intrinsical Policy Engine distribution are
documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and distribution versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Changed

- Breaking: rename profile `trace_back_to.evidences` to `trace_back_to.articles`,
  accepting only declared article IDs, without a compatibility alias. Coverage
  reports use `required_articles`, `covered_articles` and `missing_articles`;
  the `CI-004` diagnostic now reports `articles=`. Update existing pack profiles
  to the new field before compiling them with this candidate.
- Adopt pack format 3 and starter version 0.3.0. Selection belongs to each
  `action.when`; update packs and examples together without compatibility aliases.
- Install the engine under `intrinsical_policy_engine` from the `src/` layout.
  Wheel and source distribution omit packs, demos, docs directories, and
  development scripts; installed acceptance uses external checkout fixtures.
- Apply shared semantic validation to Python bundles and YAML compilation,
  including engine requirements, canonical paths, and cross-references.
- Add snapshot format version 1 and require the summary's embedded trace to
  match the separate trace document exactly as JSON. Re-export older snapshots
  from original inputs.
- Plan all selected outputs before staging; reject engine-reserved paths and
  file/directory collisions, with unconditional collisions also reported by lint.
- Keep export settings typed through providers and move evidence collection out
  of the CLI layer.
- Document Linux with Python 3.11 and 3.12 as the supported execution matrix;
  exclusive directory publication requires `renameat2` support.
- Remove unused internal Python surface: `YamlContractsAdapter.load`,
  `load_bundle_profiles` and `validate` (use `compile()`), the stdlib logger
  adapter, the unreachable exporter-not-found error, and seal report fields that
  were never reported. Exporters receive typed configuration at construction.

### Fixed

- Preserve article labels such as `P1` and `PA` in the Asana CSV instead of
  relabeling them as priorities.
- Report each missing evidence requirement once, with its action, in the export
  manifest; `assess --check-ci` and `export --check-ci` now report the same
  evidence and coverage problems.

- Redact every configured API header value, including custom names, independently
  of the raw-input opt-in. Validate nested IDs and HTTP header syntax and controls.
- Reject malformed CSV quoting, blank headers, and content-free rows. CSV quality
  uses data rows only: two to four is draft and five or more is ready.
- Exclude Markdown front matter from body quality even when its closing
  delimiter is at EOF.
- Reject relative date magnitudes and resolved dates outside the supported range
  with contextual validation errors.
- Export calendar events at the date-range boundaries using a one-day duration.
- Reject cyclic and excessive structured document graphs and detect placeholders
  inside YAML tagged collections. Blank and null-only nested values do not
  contribute to structured evidence readiness; zero and false remain valid.
- Append a separator after a valid JSONL object at EOF without a newline; reject
  a truncated trailing record without appending.
- Supply optional `regulatory_meta` as `none` and `regulatory_warnings` as an
  empty list when no authored metadata exists. Warn on unreachable positive
  question activation conditions.

### Added

- Independent EU AI Act process mock with generic intake, review, and signoff,
  synthetic evidence, and expected technical scenarios. It has no legal fidelity
  and does not import or reproduce the complete framework maintained elsewhere.
- Model-derived JSON Schemas with a synchronization check, a deterministic
  synthetic benchmark report, and reproducible authoring, signing, recovery,
  and installed-release runbooks.
- Bounded property and parser fuzz checks, an installed mock conformance runner,
  executable documentation checks, and explicit comparable benchmark budgets.
  CI retains installed acceptance and conformance receipts on failure.
- Inspect every named rule family, with explicit family selection for ambiguous
  IDs, and check explanations against independently authored mock expectations.
- Exercise actual interrupted writers and competing processes. Execute every
  documented Bash/Python block in a disposable checkout, including build and
  signing recipes, without overwriting the source checkout's artifacts.
- Build a single wheel/source pair for both CI interpreters and assemble a
  reviewable candidate from artifact-bound acceptance, conformance, coverage and
  documentation receipts. Human acceptance and publication remain separate.

### Removed

- `rules.packs` / `add_actions`, duplicate `ContractBundle.version`,
  `reference_files`, always-empty missing-reasons metadata, and unused helpers.
- Runtime `src` and `scripts` package names. Import `intrinsical_policy_engine`
  and keep development scripts in the checkout.

## [3.0.0] - 2026-10-10

### Changed

- Reduce the supported CLI to `lint`, `assess`, `export`, `seal`, and `inspect`.
- Use pack format 2, manifest-owned versions, and an enforced `requires_engine`
  constraint. The educational starter pack is version 0.2.0.
- Require structured YAML rule conditions and positive derivations.
- Validate one typed input source: question answers or explicit declared flags.
- Sanitize input once before destinations and retain raw input only through
  `--include-raw-input`.
- Require output directories that do not exist yet and explicit reviewed evidence
  for `--check-ci`; copy that evidence into the snapshot.
- Inherit evidence through article links declared from either direction, require
  ready content for every required path, and derive missing requirements from
  actual snapshot files. Missing evidence always fails sealing.
- Reject nonfinite input metadata and detect placeholders in nested structured
  evidence and CSV cells. Enumerate Git paths without filename quoting loss.
- Use one root checksum inventory and require successful signing by default.
  Unsigned seals require `--no-sign`; archive output paths must be new and external.
- Read the engine version from installed metadata generated from pyproject.
- Move development dependencies to a dependency group, remove unused runtime
  packages, and require a setuptools backend supporting the SPDX license field.
- Validate Python 3.11 and 3.12 with complete installed wheel and source archive
  flows outside the source checkout. Scan source and both distribution formats.
- Document fresh-path quickstarts, checkout/artifact installation, all target
  settings, and checked JSON/YAML answer and flag examples.
- Report command results and warnings, give field-specific corrective diagnostics,
  and support private operational JSONL logs with distinct run IDs on all commands.

### Removed

- UI, wizard, graph, simulation, auxiliary command groups, secondary renderer,
  persistent plan store, and standalone CSV exporter.
- Duplicate validation commands, version files, optional schema validation,
  permissive input aliases, environment defaults, and compatibility fallbacks.
- Export modes, release gating, fingerprints, and duplicate seal inventories.

## [2.0.1+public.0] - 2026-07-18

### Fixed

- Allow the tracked release metadata files in the public-tree guard.
- Point CLI examples at the starter demo shipped in this repository.
- Validate the generated evidence-quality report by its required maps, avoiding
  a false sealing warning when those maps are valid but empty.
- Report unknown runtime AST operators as evaluation errors rather than parse
  errors.
- Package the CLI and shared implicit subpackages so the built wheel runs
  outside the source tree.
- Use native non-blocking filesystem locks on both Unix and Windows when
  persisting plans.

### Changed

- Add negative coverage for the public release guard, evidence-quality report,
  and rule evaluation contract.
- Set the initial test coverage regression threshold to 19 percent, matching
  the fully packaged source-tree baseline.
- Document the temporary public-to-private environment-variable fallbacks.

## [2.0.0+public.0] - 2026-07-14

### Added

- First clean public distribution of the framework-neutral engine.
- Educational starter pack, demo answers, authoring documentation, CI, and
  public-boundary scanner.

### Changed

- Refined domain exports, adapter plumbing, project metadata, formatting, and
  type-checking configuration in the post-release stabilization commits.

[2.0.1+public.0]: https://github.com/Intrinsical-AI/intrinsical-policy-engine/compare/v2.0.0-public.0...v2.0.1-public.0
[2.0.0+public.0]: https://github.com/Intrinsical-AI/intrinsical-policy-engine/releases/tag/v2.0.0-public.0
