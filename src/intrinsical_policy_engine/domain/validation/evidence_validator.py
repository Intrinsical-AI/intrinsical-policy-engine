# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Validator for evidence map integrity (INV-B2).

SCOPE: ContractBundle ↔ Filesystem

This validator ensures that evidence_map.yml keys (article/action IDs)
are valid AND that referenced template files actually exist on disk.
"""

from pathlib import Path

from intrinsical_policy_engine.domain.ports import ContractBundle
from intrinsical_policy_engine.domain.validation.filesystem_interface import (
    FileExistsChecker,
    RealFileSystem,
)


def validate_evidence_map_integrity(
    bundle: ContractBundle,
    evidence_dir: Path,
    fs: FileExistsChecker | None = None,
) -> list[str]:
    """Validate that evidence map keys exist and referenced files exist.

    Checks:
    1. Keys: Every key in evidence_map must be a valid Article ID or Action ID.
    2. Values: Every path referenced in evidence_map must physically exist in the
       framework evidence template root.

    Args:
        bundle: Contract bundle to validate
        evidence_dir: Pre-resolved evidence template root from the adapter layer.
        fs: Optional filesystem abstraction for testing. Defaults to RealFileSystem.

    Returns:
        List of error strings. Empty list if no problems found.
    """
    # Default to real filesystem if not provided
    if fs is None:
        fs = RealFileSystem()

    problems: list[str] = []
    evidence_map = bundle.evidence_map or {}

    # Build sets of valid IDs
    valid_article_ids = {article.id for article in bundle.articles.taxonomy}
    valid_action_ids = {action.id for action in bundle.actions.actions}

    bundle_path = Path(bundle.path).resolve()
    evidence_root_rel = evidence_dir.relative_to(bundle_path).as_posix()

    for key, evidences in evidence_map.items():
        # Check Key validity
        if key not in valid_article_ids and key not in valid_action_ids:
            problems.append(
                f"[EVIDENCE][ERROR] Evidence map key '{key}' not found in Articles or Actions."
            )

        # Check file existence (using injected filesystem)
        for evidence in evidences:
            # evidence is a dict with 'path', 'required', etc.
            # normalized by load_evidence_map
            rel_path = evidence.get("path")
            if not rel_path:
                continue

            full_path = evidence_dir / rel_path
            if not fs.exists(full_path):
                problems.append(
                    f"[EVIDENCE][WARN] Evidence template not found: {rel_path} "
                    f"(referenced by '{key}') - expected at {evidence_root_rel}/{rel_path}"
                )
                continue

            is_dir = getattr(fs, "is_dir", None)
            if not callable(is_dir):
                continue

            expects_directory = str(rel_path).endswith("/")
            resolves_to_directory = bool(is_dir(full_path))
            if expects_directory and not resolves_to_directory:
                problems.append(
                    f"[EVIDENCE][WARN] Evidence path '{rel_path}' is declared as a directory "
                    f"but does not resolve to one (referenced by '{key}')."
                )
            elif resolves_to_directory and not expects_directory:
                problems.append(
                    f"[EVIDENCE][WARN] Evidence directory path '{rel_path}' must end with '/' "
                    f"to use directory expansion semantics (referenced by '{key}')."
                )

    return problems
