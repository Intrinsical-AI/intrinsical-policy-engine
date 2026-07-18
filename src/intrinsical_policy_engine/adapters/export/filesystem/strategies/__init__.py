# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Export strategies for FilesystemExporter decomposition.

This module implements the Strategy pattern to decompose the monolithic
FilesystemExporter into cohesive, testable units.

Architecture note:
- ExportContext lives in adapters (infrastructure concerns)
- EvalContext lives in domain (business logic)
- ExportContext CONTAINS EvalContext (composition, not inheritance)

Pipeline order (defined in FilesystemExporter):
1. BundleProfileStrategy - Declarative bundles (L3)
2. EvidenceStrategy - Evidence zips & quality metrics
3. BacklogStrategy - CSVs/MDs/Summary (uses metrics from step 2)
4. ManifestStrategy - Fingerprints & ICS (seals the output)
"""

from intrinsical_policy_engine.adapters.export.filesystem.strategies.base import (
    ExportContext,
    ExportStrategy,
)
from intrinsical_policy_engine.adapters.export.filesystem.strategies.bundle_profile import (
    BundleProfileStrategy,
)
from intrinsical_policy_engine.adapters.export.filesystem.strategies.evidence import (
    EvidenceStrategy,
)
from intrinsical_policy_engine.adapters.export.filesystem.strategies.manifest import (
    ManifestStrategy,
)

__all__ = [
    "BundleProfileStrategy",
    "EvidenceStrategy",
    "ExportContext",
    "ExportStrategy",
    "ManifestStrategy",
]
