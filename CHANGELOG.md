# Changelog

All notable changes to the public Intrinsical Policy Engine distribution are
documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and distribution versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

The entries in this section describe the local `3.0.0a1` development line.
They do not represent a published alpha release or a remote-CI result.

### Added

- Introduce the canonical `intrinsical_policy_engine` Python namespace and
  module entry point for the 3.0 line.
- Add source-tree and built-wheel package contracts, CLI-surface
  characterizations, and regressions for mixed rule depth and typed linting.
- Add the 3.0 architecture decision and migration guide.
- Add a supported typed embedding facade for pack validation, assessment,
  export, sealing, diagnostics, and composable gate reports.
- Add optional `ProductIdentity` provenance for embedding products without
  conflating product, engine, pack, or artifact-schema versions.
- Ship the PEP 561 `py.typed` marker in the canonical runtime package.

### Changed

- Package only `intrinsical_policy_engine` at runtime; the generic `src`
  namespace is no longer installed.
- Move template-integrity runtime code into the package and retain the
  repository script as a thin wrapper.
- Remove public maintainer commands whose implementations are not shipped.
- Make strict lint run the same typed contract validation exercised by load and
  assessment.
- Require pack engine compatibility as PEP 440 specifiers, validate pack-local
  license metadata, and isolate all process-environment reads at composition
  boundaries.
- Centralize export acceptance policy so the CLI and embedding facade cannot
  disagree about release, quality, coherence, or target failures.
- Make raw-answer persistence opt-in through `ExportRequest.include_raw_answers`
  or CLI `--include-raw-answers`; omit the raw-answer metadata file by default.
- Require strict policy for release exports and reject coverage or signing
  bypasses before writing release output.
- Emit the same artifact-schema and pack provenance envelope from the public
  CLI and the embedding API.

### Fixed

- Preserve the combined nesting-depth budget when dictionary conditions enter
  cached string parsing.
- Make the public release guard inspect tracked and untracked, non-ignored files
  while excluding deleted paths.
- Reject framework-pack, bundle, evidence and reused-output path escapes,
  including symbolic links, before hashing, rendering, copying or sealing.
- Fail strict/release exports and strict seals when signing was requested but
  GPG is unavailable, no secret key exists, or no signature is created.
- Apply pack compatibility/license checks and output/pack isolation uniformly
  across the embedding API and maintained CLI commands before any output write.
- Remove stale raw-answer metadata when an output directory is reused without
  the explicit persistence opt-in.
- Validate exact wheel and sdist contents and build the release-candidate wheel
  from the inspected source distribution in CI.

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
