# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Deduplication service: resolve action ID aliases to canonical IDs."""

from dataclasses import dataclass, field

from intrinsical_policy_engine.domain.contract_models import DedupMapping, DedupsContract
from intrinsical_policy_engine.domain.types import ActionId


@dataclass(frozen=True)
class DedupResult:
    """Result of deduplication with audit trail.

    Attributes:
        canonical_ids: Final list of canonical action IDs (deduplicated).
        aliases_resolved: Dict mapping alias -> canonical for aliases that were resolved.
        duplicates_removed: List of IDs that were removed as duplicates.
    """

    canonical_ids: tuple[ActionId, ...]
    aliases_resolved: dict[ActionId, ActionId] = field(default_factory=dict)
    duplicates_removed: tuple[ActionId, ...] = field(default_factory=tuple)


def dedupe_ids(ids: list[ActionId], dedups: DedupsContract) -> list[ActionId]:
    """Deduplicate action IDs by resolving aliases to canonical IDs.

    Resolves aliases to their canonical forms and removes duplicates, preserving
    the order of first occurrence.

    Args:
        ids: List of action IDs (may contain aliases and duplicates).
        dedups: Deduplication config model with typed DedupMapping entries.

    Returns:
        List of canonical action IDs (deduplicated, preserving order of first
        occurrence).

    Example:
        >>> ids = ["CTRL-9-RMS", "RMS-LEGACY", "CTRL-9-RMS"]
        >>> dedups = DedupsContract(
        ...     mappings=[DedupMapping(alias="RMS-LEGACY", canonical="CTRL-9-RMS")]
        ... )
        >>> result = dedupe_ids(ids, dedups)
        >>> assert result == ["CTRL-9-RMS"]
    """
    result = dedupe_ids_with_trace(ids, dedups)
    return list(result.canonical_ids)


def dedupe_ids_with_trace(ids: list[ActionId], dedups: DedupsContract) -> DedupResult:
    """Deduplicate action IDs with full audit trail.

    Like dedupe_ids(), but returns a DedupResult with complete traceability
    information for audit purposes.

    Args:
        ids: List of action IDs (may contain aliases and duplicates).
        dedups: Deduplication config model with typed DedupMapping entries.

    Returns:
        DedupResult containing:
            - canonical_ids: Final deduplicated list (tuple)
            - aliases_resolved: Dict mapping alias -> canonical for resolved aliases
            - duplicates_removed: Tuple of IDs that were removed as duplicates

    Note:
        This supports tests/CONTRACTS.md Level 4 traceability requirements.
        First occurrence of each canonical ID is preserved; subsequent duplicates
        are tracked in duplicates_removed.
    """
    alias_to_canonical: dict[str, str] = {}

    if not hasattr(dedups, "mappings"):
        raise TypeError("dedups must be a DedupsContract")

    mappings = dedups.mappings

    for mapping in mappings:
        if not isinstance(mapping, DedupMapping):
            raise TypeError("dedups.mappings entries must be DedupMapping models")

        alias = mapping.alias
        canonical = mapping.canonical

        alias_to_canonical[alias] = canonical

    # Pass 1: Resolve all aliases to canonicals and record alias resolution map
    resolved_ids: list[ActionId] = []
    aliases_resolved: dict[ActionId, ActionId] = {}

    for action_id in ids:
        canonical = alias_to_canonical.get(action_id, action_id)
        resolved_ids.append(canonical)
        if action_id != canonical:
            aliases_resolved[action_id] = canonical

    # Pass 2: Deduplicate (first-seen wins)
    # To be deterministic and consistent in audit:
    # we want to keep the FIRST occurrence of a canonical ID + report inputs dropped.

    unique_ids: list[ActionId] = []
    seen: set[ActionId] = set()
    duplicates_removed: list[ActionId] = []

    for original_id, canonical in zip(ids, resolved_ids, strict=False):
        if canonical in seen:
            # It's a duplicate of something already kept
            duplicates_removed.append(original_id)
        else:
            unique_ids.append(canonical)
            seen.add(canonical)

    return DedupResult(
        canonical_ids=tuple(unique_ids),
        aliases_resolved=aliases_resolved,
        duplicates_removed=tuple(duplicates_removed),
    )
