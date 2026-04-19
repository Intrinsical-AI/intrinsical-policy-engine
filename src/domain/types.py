# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Semantic type aliases for domain entities.

These aliases provide context about what a string or dictionary represents,
improving function signature readability without runtime overhead.
"""

from typing import Any, TypeAlias, TypedDict

# Basic identifiers.
Flag: TypeAlias = str
ActionId: TypeAlias = str
ArticleId: TypeAlias = str
QuestionId: TypeAlias = str
PackId: TypeAlias = str

# Complex structures.

# A dictionary representing a raw question/answer document or section.
QuestionnaireDoc: TypeAlias = dict[str, Any]

# A dictionary representing user answers (key=QuestionId, value=Answer).
UserAnswers: TypeAlias = dict[QuestionId, Any]

# A parsed "when" condition AST (structure depends on the rule_engine implementation).
ASTNode: TypeAlias = tuple[str, Any] | None
ConditionAST: TypeAlias = ASTNode


class Trace(TypedDict, total=False):
    """Structure of the execution trace.

    Captures the complete audit trail of assessment execution including inputs,
    intermediate states, and outputs. All collections are sorted for deterministic
    output and reproducibility.
    """

    answers_raw: dict[
        str, Any
    ]  # Contains answers_hash, meta, optionally answers_keys/answers_sanitized
    flags: dict[str, Any]  # Contains emitted, final, and optionally derived flags
    rules_applied: dict[str, Any]  # Contains packs_fired, halted, stops
    actions: dict[str, Any]  # Contains selected and optionally deduped action IDs
    articles_overlay: dict[ArticleId, list[ActionId]]
    due_hints: dict[ActionId, str]
    derivations_applied: list[dict[str, Any]]  # List of derivation records
    rules_evaluated: int
    actions_deduped: dict[str, Any]
    engine_version: str
    contracts_version: str
    framework_version: str
    bundle_hash: str
    framework_pack_hash: str
    pack_hashes: dict[str, str]  # Pack ID -> hash mapping
    plan_hash: str
    templates_hash: str
    assessment_timestamp: str


class Plan(TypedDict, total=False):
    """Structure of the assessment plan output.

    This TypedDict documents the complete shape of the Plan returned by
    assess_from_bundle(). All fields are optional (total=False) to support
    partial plans during construction.

    Key fields added per review feedback:
    - assessment_timestamp: ISO 8601 timestamp of when assessment was run
    - export_context: Namespace and project metadata added during export

    Trace sub-fields (documented for reference, access via plan["trace"]):
    - bundle_hash: SHA256 of all contracts for drift detection
    - plan_hash: Deterministic hash of plan (excludes volatile fields)
    - engine_version: Version of the assessment engine
    - framework_version: Version of the contracts framework
    - contracts_version: Semantic version from contracts bundle
    - assessment_timestamp: When the assessment was performed
    - warnings: Dict of warning types to warning messages
    """

    # Core assessment outputs.
    flags: list[Flag]
    actions: list[ActionId]
    actions_meta: list[dict[str, Any]]
    due_hints: dict[ActionId, str]

    # Article mappings.
    articles_overlay: dict[ArticleId, list[ActionId]]
    flag_article_index: dict[Flag, list[ArticleId]]

    # Evidence mappings.
    articles_evidence_map: dict[ArticleId, list[str]]
    actions_evidence_map: dict[ActionId, list[str]]
    section_refs_by_flag: dict[Flag, list[ArticleId]]

    # Legal and routing.
    legal_token: dict[str, Any]
    routing: dict[str, Any]

    # Classification.
    outcome: list[str]
    outcome_axes: dict[str, Any]
    system_profile: dict[str, Any]  # Serialized SubjectProfile

    # Traceability (docs/invariants/ENGINE-ARCHITECTURE-v1.md).
    audit: dict[str, Any]
    trace: dict[str, Any]
    assessment_timestamp: str  # ISO 8601 UTC timestamp

    # Template metadata (demographic/contextual).
    system: dict[str, Any]
    provider: dict[str, Any]
    declared_by: dict[str, Any]
    approvals: dict[str, Any]

    # Export context (added during export orchestration).
    export_context: dict[str, Any]


# =============================================================================
# VALIDATION HELPERS
# =============================================================================


def validate_plan_keys(plan: Plan) -> list[str]:
    """Validate that plan keys match expected TypedDict fields.

    This helper catches typos and unknown keys at runtime, supplementing
    the static typing provided by TypedDict. Per docs/invariants/ENGINE-ARCHITECTURE-v1.md,
    Plan uses TypedDict for zero-overhead typing, but this function can
    catch runtime errors like `plan.get("confrmity")` (typo).

    Args:
        plan: A plan dictionary to validate

    Returns:
        List of warning messages for unknown keys. Empty if all keys are valid.

    Example:
        >>> plan = {"flags": [...], "conformty": {...}}  # typo!
        >>> warnings = validate_plan_keys(plan)
        >>> warnings
        ["Unknown key in plan: 'conformty' (did you mean 'routing'?)"]
    """
    valid_keys = set(Plan.__annotations__.keys())
    actual_keys = set(plan.keys())
    unknown = actual_keys - valid_keys

    warnings: list[str] = []
    for key in sorted(unknown):
        # Try to suggest similar keys for likely typos
        suggestion = _find_similar_key(key, valid_keys)
        if suggestion:
            warnings.append(f"Unknown key in plan: '{key}' (did you mean '{suggestion}'?)")
        else:
            warnings.append(f"Unknown key in plan: '{key}'")

    return warnings


def _find_similar_key(unknown: str, valid_keys: set[str]) -> str | None:
    """Find the most similar valid key using simple heuristics."""
    # Simple substring matching for common typos
    for valid in valid_keys:
        # Check if one is substring of the other (off-by-one typos)
        if unknown in valid or valid in unknown:
            return valid
        # Check if they start the same way (truncation typos)
        if len(unknown) >= 3 and len(valid) >= 3 and unknown[:3] == valid[:3]:
            return valid
    return None
