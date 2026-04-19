# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Coverage Context and ID Canon for Generative System (Contract v2).

This module defines the context required to evaluate coverage rules and the
canonical ID generation logic to ensure determinism across the pipeline.
"""

from dataclasses import dataclass, field
from typing import Any

from src.domain.core.subject_profile import SubjectProfile


@dataclass(frozen=True)
class CoverageContext:
    """Context for evaluating coverage rules against a specific subject state.

    This replaces the implicit "role + flags" checks with an explicit context
    entity that can be passed to rule evaluators.
    """

    role: str
    system_profile: SubjectProfile
    flags: dict[str, bool] = field(default_factory=dict)

    # Optional override context for advanced scenarios
    overrides: dict[str, Any] = field(default_factory=dict)

    @property
    def outcome(self) -> str:
        """Helper to get the main classification outcome (risk tier)."""
        return self.system_profile.classification_tier


# =============================================================================
# ID CANON (V1) - Deterministic ID Generation
# =============================================================================


def canonical_node_id(role: str, article: str) -> str:
    """Generate stable BundleNode ID for a rule application.

    Format: node_{role}_{article}
    Example: node_provider_art11
    """
    role_clean = role.lower().strip()
    art_clean = article.lower().replace("-", "").replace("article", "art")
    return f"node_{role_clean}_{art_clean}"
