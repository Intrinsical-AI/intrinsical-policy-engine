# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Domain service for determining which artifacts to include/exclude.

This module extracts artifact selection logic from the app/rendering layer,
making filtering decisions available for unit testing and reuse across
different export paths.

Per docs/invariants/ENGINE-ARCHITECTURE-v1.md, this belongs in the Artefactos y Entregables domain
but represents pure business logic that should not depend on infrastructure.
"""

from dataclasses import dataclass
from pathlib import Path

# Role-based evidence filtering - moved from artifact_renderer.py
# Maps directory names in the evidence template root to flag patterns that activate them.
ROLE_DIRECTORIES: dict[str, set[str]] = {
    "deployer": {"role.operator"},
    "provider": {"role.source"},
    "model": {"model.provider", "model.systemic_risk", "model.systemic_risk_candidate"},
    "importer": {"role.importer"},
    "distributor": {"role.distributor"},
}

# Directories that are always included regardless of role
ALWAYS_INCLUDE_DIRS: set[str] = {"common", "04_Literacy", "examples"}


@dataclass(frozen=True)
class SelectionResult:
    """Result of an artifact selection decision."""

    should_include: bool
    reason: str


class ArtifactSelector:
    """Determines artifact inclusion based on plan roles and active evidence.

    This class encapsulates the filtering logic previously in artifact_renderer.py,
    enabling:
    - Isolated unit testing of selection rules
    - Reuse across different export paths (CLI, API, strategies)
    - Clear separation between domain logic and rendering infrastructure
    """

    def __init__(
        self,
        role_directories: dict[str, set[str]] | None = None,
        always_include_dirs: set[str] | None = None,
    ) -> None:
        """Initialize selector with optional custom role/include mappings.

        Args:
            role_directories: Override default ROLE_DIRECTORIES for testing.
            always_include_dirs: Override default ALWAYS_INCLUDE_DIRS for testing.
        """
        self._role_directories = role_directories or ROLE_DIRECTORIES
        self._always_include_dirs = always_include_dirs or ALWAYS_INCLUDE_DIRS

    def get_active_roles(self, flags: list[str]) -> set[str]:
        """Determine which roles are active based on flags.

        Args:
            flags: List of active flag strings from the plan.

        Returns:
            Set of active role directory names (e.g., {"deployer", "provider"}).
            Returns empty set if no role flags match.
        """
        active: set[str] = set()
        for role_dir, role_flags in self._role_directories.items():
            if any(f in flags for f in role_flags):
                active.add(role_dir)
        return active

    def should_include_evidence_path(
        self,
        rel_path: Path | str,
        active_roles: set[str],
        active_evidence: set[str] | None = None,
    ) -> SelectionResult:
        """Check if an evidence template path should be included.

        Filters by:
        1. Role (deployer, provider, model, etc.) - always applies
        2. Active evidence paths (from actions) - if provided, only include matching

        Args:
            rel_path: Relative path from templates directory.
            active_roles: Set of active role directories.
            active_evidence: Optional set of evidence paths from active actions.

        Returns:
            SelectionResult with decision and reason.
        """
        if isinstance(rel_path, str):
            rel_path = Path(rel_path)

        parts = rel_path.parts
        if len(parts) < 2:
            return SelectionResult(True, "not_evidence_template")

        if len(parts) >= 3 and parts[0] == "evidence" and parts[1] == "templates":
            evidence_parts = parts[2:]
        else:
            return SelectionResult(True, "not_evidence_template")

        if not evidence_parts:
            return SelectionResult(True, "not_evidence_template")

        subdir = evidence_parts[0]

        # Always include common directories
        if subdir in self._always_include_dirs:
            return SelectionResult(True, f"always_include:{subdir}")

        # Role-based filtering
        if subdir not in active_roles:
            return SelectionResult(False, f"role_mismatch:{subdir}")

        # If no active_evidence filter provided, include based on role only
        if active_evidence is None or not active_evidence:
            return SelectionResult(True, "role_match_no_evidence_filter")

        # Evidence-based filtering
        rel_str = "/".join(evidence_parts)

        # Direct match
        if rel_str in active_evidence:
            return SelectionResult(True, "direct_evidence_match")

        # Check parent/child relationships
        rel_dir = rel_str + "/" if not rel_str.endswith("/") else rel_str
        for ev in active_evidence:
            if ev.startswith(rel_dir) or rel_str.startswith(ev.rstrip("/")):
                return SelectionResult(True, "evidence_path_match")

        # Check subdirectory level matching
        for ev in active_evidence:
            ev_parts = ev.split("/")
            if (
                len(evidence_parts) > 0
                and len(ev_parts) > 0
                and "/".join(ev_parts[: len(evidence_parts)]) == rel_str.rstrip("/")
            ):
                return SelectionResult(True, "evidence_subdir_match")

        return SelectionResult(False, "no_evidence_match")


def get_active_evidence_paths(plan: dict) -> set[str]:
    """Extract evidence paths from active actions.

    Collects all evidence template paths referenced by active actions in the plan.
    Used to determine which evidence templates should be included in exports.

    Args:
        plan: Plan dictionary containing 'actions_meta' list with action metadata.

    Returns:
        Set of normalized evidence paths (relative to the evidence template root).
        Empty set if no actions_meta or no evidence paths found.
    """
    active_evidence: set[str] = set()
    actions_meta = plan.get("actions_meta", [])

    if not isinstance(actions_meta, list):
        return active_evidence

    for action in actions_meta:
        if not isinstance(action, dict):
            continue
        evidence_list = action.get("evidence", [])
        if not isinstance(evidence_list, list):
            continue
        for ev in evidence_list:
            if isinstance(ev, str) and ev:
                # Normalize path: remove leading slashes, convert to forward slashes
                normalized = ev.strip().lstrip("/").replace("\\", "/")
                active_evidence.add(normalized)
                # Also add parent directories for directory-based matching
                parts = normalized.split("/")
                for i in range(1, len(parts)):
                    active_evidence.add("/".join(parts[:i]) + "/")

    return active_evidence


# Convenience singleton for simple usage (matches previous pattern)
# Tests should instantiate their own ArtifactSelector for isolation.
ARTIFACT_SELECTOR = ArtifactSelector()
