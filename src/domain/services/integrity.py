# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Integrity services for deterministic hashing of plans and bundles.

This module provides pure functions for computing cryptographic hashes
that ensure reproducibility and traceability per docs/invariants/ENGINE-ARCHITECTURE-v1.md.

Design:
    - No I/O: operates on in-memory structures only
    - No domain imports beyond types: prevents circular dependencies
    - Deterministic: same input always produces same hash
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from src.domain.ports import ContractBundle


# Fields excluded from plan hash (volatile/non-deterministic)

# DESIGN RATIONALE:
# - `system_profile` (NOT volatile): Contains canonical derived profile (risk_tier, roles,
#   regimes, name). Changes here reflect semantic changes to the assessment.
# - `system` (volatile): Raw metadata from answers.json for template rendering.
#   Contains potentially variable format fields. The semantic info is in system_profile.
# - `provider` (volatile): Raw metadata for template personalization, not plan semantics.

# If you add/remove fields, update tests/CONTRACTS.md §4 (determinism invariants).
_VOLATILE_PLAN_FIELDS = frozenset(
    {
        "assessment_timestamp",  # Runtime timestamp, obviously non-deterministic
        "export_context",  # Export-time context, not assessment semantics
        "audit",  # CLI/export audit metadata (paths, local hashes)
        "system",  # Raw metadata for templates; canonical data in system_profile
        "provider",  # Raw metadata for templates; not part of assessment semantics
        "declared_by",  # CLI injection from answers.json for metadata
        "approvals",  # CLI injection from answers.json for metadata
        "wizard_answers",  # Legacy injection; answers live in external files / trace hashes
        "plan_hash",  # Trace integrity hash itself must not influence deterministic content hash
    }
)


def compute_plan_hash(plan_data: dict[str, Any]) -> str:
    """Compute deterministic hash of plan excluding volatile fields.

    Per /ENGINE-ARCHITECTURE-v1.md and CONTRACTS:
    same inputs must produce the same plan_hash. This function excludes fields
    that vary between runs (timestamps, export context) to ensure reproducibility.

    Args:
        plan_data: The plan dictionary (before adding volatile fields like
            assessment_timestamp). Should contain flags, actions, overlay, etc.

    Returns:
        SHA256 hex digest (64-character hex string) of the deterministic plan
        content. Same inputs always produce the same hash.

    Note:
        Volatile fields excluded: assessment_timestamp, export_context, audit,
        system, wizard_answers, plan_hash, trace.answers_raw.
    """

    def _strip_volatile(obj: dict) -> dict:
        """Recursively strip volatile fields from dict.

        In addition to top-level volatile fields, this removes trace.answers_raw so that
        plan_hash depends only on the semantic plan (flags/actions/overlay/etc.) and not
        on the concrete encoding of answers. This matches property tests that blank out
        answers_raw before hashing for equivalence checks.
        """
        result = {}
        for k, v in obj.items():
            if k in _VOLATILE_PLAN_FIELDS:
                continue
            if k == "trace" and isinstance(v, dict):
                # Strip volatile fields and raw answers from trace too
                trace_copy = {
                    tk: tv
                    for tk, tv in v.items()
                    if tk not in _VOLATILE_PLAN_FIELDS and tk != "answers_raw"
                }
                result[k] = trace_copy
            elif isinstance(v, dict):
                result[k] = _strip_volatile(v)
            else:
                result[k] = v
        return result

    clean_plan = _strip_volatile(plan_data)
    serialized = json.dumps(clean_plan, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode()).hexdigest()


def compute_bundle_hash(bundle: ContractBundle) -> str:
    """Compute deterministic hash of bundle contracts for traceability.

    Hashes the serialized representation of ALL contracts that affect plan output:
    - Core: flags, rules, actions, articles, due_rules, calendar, dedups
    - Inputs: questions (affects flag derivation), evidence_map (affects gating)
    - Metadata: audit (affects compliance context), risk_config
    - Runtime: semantics/policies/presentation loaded from the active framework pack

    This provides a reproducibility fingerprint without requiring disk I/O.

    Args:
        bundle: ContractBundle with loaded contracts. Must have 'version' attribute
            and contract attributes (flags, rules, actions, etc.).

    Returns:
        SHA256 hex digest (64-character hex string) of the bundle content.
        Same contracts + same version => same hash, regardless of filesystem location.

    Note:
        Intentionally excludes bundle.path to ensure path-invariance for
        reproducibility (docs/invariants/ENGINE-ARCHITECTURE-v1.md, INV-05).
        Changes to runtime, questions, or evidence_map must trigger bundle
        coherence failures in export, since they alter the plan or gating behavior.
    """

    def _normalize_for_hash(value: Any) -> Any:
        """Normalize contracts for stable JSON hashing.

        Recursively normalizes nested data structures for deterministic hashing.
        Handles arbitrary nested structures (dicts, lists, sets, tuples, primitives).

        - Converts all dict keys to strings so json.dumps(sort_keys=True) never compares
          heterogeneous key types.
        - Recurses into nested dicts/lists.
        - Converts sets/frozensets/tuples to sorted lists for determinism.

        Args:
            value: Arbitrary value (dict, list, set, tuple, or primitive).

        Returns:
            Normalized value suitable for deterministic JSON serialization.
        """
        if isinstance(value, dict):
            return {str(k): _normalize_for_hash(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            # Recurse then return as list (tuples become lists in JSON)
            return [_normalize_for_hash(v) for v in value]
        if isinstance(value, (set, frozenset)):
            # Sort sets for determinism
            try:
                return sorted([_normalize_for_hash(v) for v in value])
            except TypeError:
                # Fallback for unsortable mixed types: convert to string then sort
                return sorted([str(_normalize_for_hash(v)) for v in value])
        return value

    h = hashlib.sha256()
    # Include version as part of the hash (path intentionally excluded for reproducibility)
    h.update(f"version:{bundle.version}".encode())

    # Hash ALL contracts in deterministic order
    # Use model_dump() for Pydantic models, fallback to dict/list for plain structures
    contracts_to_hash = [
        ("flags", bundle.flags),
        ("rules", bundle.rules),
        ("actions", bundle.actions),
        ("articles", bundle.articles),
        ("due_rules", bundle.due_rules),
        ("calendar", bundle.calendar),
        ("dedups", bundle.dedups),
        ("questions", getattr(bundle, "questions", None)),
        ("evidence_map", getattr(bundle, "evidence_map", None)),
        ("audit", getattr(bundle, "audit", None)),
        ("risk_config", getattr(bundle, "risk_config", None)),
        ("runtime", getattr(bundle, "runtime", None)),
    ]

    for name, contract in contracts_to_hash:
        if contract is None:
            # Skip None/absent contracts (e.g., optional dedups or audit)
            continue
        if hasattr(contract, "model_dump"):
            raw = contract.model_dump()
        elif isinstance(contract, (dict, list)):
            raw = contract
        else:
            raw = {}
        data = _normalize_for_hash(raw)
        serialized = json.dumps(data, sort_keys=True, ensure_ascii=False)
        h.update(f"{name}:{serialized}".encode())

    return h.hexdigest()
