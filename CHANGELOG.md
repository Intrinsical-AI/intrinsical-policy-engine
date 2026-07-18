# Changelog

All notable changes to the public Intrinsical Policy Engine distribution are
documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and distribution versions follow [Semantic Versioning](https://semver.org/).

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
