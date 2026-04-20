# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Bundle models and predicate system for declarative bundle profiles.

R3P0 Fase 1: Auto-import predicates module to ensure core predicates
are registered when the bundles package is used.

Reference: docs/invariants/ENGINE-ARCHITECTURE-v1.md - BundleBlueprint & BundleProfiles
"""

# Import predicates module to trigger auto-registration of core predicates
# This ensures PREDICATES registry is populated whenever bundles are used
from src.domain.bundles import predicates as _predicates  # noqa: F401

# Public API
from src.domain.bundles.context import EvalContext
from src.domain.bundles.models import BundleNode, BundleProfile
from src.domain.bundles.registry import PREDICATES, PredicateRegistry

__all__ = [
    "PREDICATES",
    "BundleNode",
    "BundleProfile",
    "EvalContext",
    "PredicateRegistry",
]
