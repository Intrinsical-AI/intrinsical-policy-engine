# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Tracer service: build execution trace for audit and debugging."""

import hashlib
import json
from typing import Any

from src.common.sanitization import SanitizationMode, sanitize_value
from src.domain.types import ActionId, Flag


def _hash_answers(answers: dict) -> str:
    """Generate deterministic SHA256 hash of answers for verification."""
    serialized = json.dumps(answers, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _extract_answer_keys(answers: dict) -> list[str]:
    """Extract question IDs that were answered (for traceability without full payload)."""
    if not isinstance(answers, dict):
        return []
    # Handle both formats: {"answers": {...}} and direct {"Q1": "yes", ...}
    inner = answers.get("answers") if "answers" in answers else answers
    if isinstance(inner, dict):
        return sorted([k for k in inner if not k.startswith("_")])
    return []


# Keys that are explicitly allowed to have their values preserved
_SAFE_KEY_PREFIXES = frozenset(
    {
        "q_",
        "s1_",
        "s2_",
        "s3_",
        "s4_",
        "s5_",  # Question IDs
        "flag.",
        "scope.",
        "use.",
        "classification.",
        "model.",
        "role.",  # Flag-like keys
    }
)


def _sanitize_value_for_trace(
    value: str | int | float | bool | list[Any] | dict[str, Any] | None,
) -> str:
    """Sanitize a single value for trace output using shared helper.

    Args:
        value: Value to sanitize (str, int, float, bool, list, dict, or None).

    Returns:
        Sanitized string representation using hash mode.
    """
    return sanitize_value(value, mode=SanitizationMode.HASH)


def _sanitize_answers_for_trace(answers: dict) -> dict[str, Any]:
    """Extract answers for trace with conservative PII sanitization.

    Uses allowlist approach: only canonical values (yes/no/unknown, etc.)
    are preserved. All other values are hashed for verification.

    This ensures GDPR compliance by default, as free-text answers
    (which may contain PII) are never stored verbatim.
    """
    if not isinstance(answers, dict):
        return {}

    inner = answers.get("answers") if "answers" in answers else answers

    if not isinstance(inner, dict):
        return {}

    sanitized = {}
    for k, v in inner.items():
        # Skip internal fields
        if k.startswith("_"):
            continue
        sanitized[k] = _sanitize_value_for_trace(v)
    return sanitized


def _sanitize_meta_for_trace(meta: dict | None) -> dict[str, Any] | None:
    """Sanitize answers.meta for trace with same conservative approach.

    Only preserves safe structural keys (version, timestamp, etc.).
    All potentially PII-containing values are redacted.
    """
    if not isinstance(meta, dict):
        return None

    # Keys safe to preserve in meta (structural, not PII)
    safe_meta_keys = {
        "version",
        "schema_version",
        "timestamp",
        "source",
        "format",
        "questionnaire_id",
        "questionnaire_version",
    }

    sanitized = {}
    for k, v in meta.items():
        k_lower = k.lower()
        if k_lower in safe_meta_keys:
            # Even for safe keys, sanitize the value
            if isinstance(v, str) and len(v) <= 50:
                sanitized[k] = v
            else:
                sanitized[k] = _sanitize_value_for_trace(v)
        else:
            # Redact unknown meta keys entirely
            sanitized[k] = "[REDACTED]"

    return sanitized if sanitized else None


def build_trace(
    answers: dict,
    flags_emitted: set[Flag],
    final_flags: set[Flag],
    rules_applied: dict,
    selected_action_ids: list[ActionId],
    due_hints: dict,
    article_overlay: dict,
    *,
    # Extended trace fields per tests/CONTRACTS.md Level 4
    flags_derived: set[Flag] | None = None,
    derivations_applied: list[dict] | None = None,
    rules_evaluated: int | None = None,
    actions_deduped: dict | None = None,
    engine_version: str | None = None,
    contracts_version: str | None = None,
    contract_version: str | None = None,
    include_full_trace: bool = False,
    include_raw_answers: bool = False,
    templates_hash: str | None = None,
) -> dict:
    """Build execution trace capturing the assessment process.

    Constructs a comprehensive audit trail of the assessment execution, including
    inputs, intermediate states, and outputs. All collections are sorted for
    deterministic output and reproducibility.

    Args:
        answers: Raw user answers dictionary (may contain 'answers' and 'meta' keys).
        flags_emitted: Flags initially emitted from questionnaire evaluation.
        final_flags: Final flags after derivation closure.
        rules_applied: Dictionary with packs_fired, halted, stops information.
        selected_action_ids: List of action IDs selected after filtering.
        due_hints: Due date hints mapping (action_id -> date string).
        article_overlay: Article overlay mapping (article_id -> list of action_ids).
        flags_derived: Flags that were derived (not in initial set). Optional.
        derivations_applied: List of derivations that fired. Optional.
        rules_evaluated: Total count of rule evaluations. Optional.
        actions_deduped: Dictionary of deduplication results. Optional.
        engine_version: Engine version string. Optional.
        contracts_version: Contracts version string. Optional.
        contract_version: Legacy contract version (deprecated). Optional.
        include_full_trace: Whether to include detailed answers (keys and sanitized
            values) in trace. Default: False for privacy.
        include_raw_answers: Whether to include raw answers in trace. This is
            review and should only be enabled with explicit operator intent.
        templates_hash: Hash of templates used for reproducibility (INV-05). Optional.

    Returns:
        Trace dictionary with sorted, deterministic output containing:
            - answers_raw: Answers metadata, hash, and optionally sanitized values
            - flags: Emitted, final, and derived flags
            - rules_applied: Packs fired, halted status, stops
            - actions: Selected and deduped action IDs
            - articles_overlay: Article to actions mapping
            - due_hints: Due date hints
            - derivations_applied: List of derivations that fired
            - rules_evaluated: Count of rule evaluations
            - engine_version, contracts_version, templates_hash: Version info

    Note:
        Per CONTRACTS.md Level 4 (§16), the trace includes:
        - flags.derived: Flags added during derivation
        - derivations_applied: Which rules fired and what they set
        - rules_evaluated: Total count of evaluations
        - actions.deduped: Deduplication results

        Privacy: answers are sanitized by default (PII hashed). Full trace
        requires explicit include_full_trace=True. Raw answers require
        explicit include_raw_answers=True and should not be persisted by default.
    """
    answers_meta: dict | None = None
    if isinstance(answers, dict):
        maybe_meta = answers.get("meta")
        if isinstance(maybe_meta, dict):
            answers_meta = maybe_meta

    # Build comprehensive answers_raw for auditability
    # NOTE: Only metadata is kept per README; full answers are sanitized/hashed
    # Privacy update: answers_keys and answers_sanitized are only included if enabled
    answers_raw: dict[str, Any] = {}
    if isinstance(answers, dict) and answers:
        answers_raw["answers_hash"] = _hash_answers(answers)

        # Sanitize meta to prevent PII leakage (e.g., contact_email in meta)
        sanitized_meta = _sanitize_meta_for_trace(answers_meta)
        if sanitized_meta is not None:
            answers_raw["meta"] = sanitized_meta

        # Only include detailed keys/sanitized answers if explicitly requested
        # Privacy note: answers are sanitized (yes/no/values hashed), not raw PII
        if include_full_trace:
            answers_raw["answers_keys"] = _extract_answer_keys(answers)
            answers_raw["answers_sanitized"] = _sanitize_answers_for_trace(answers)

        # Raw answers are only included when explicitly enabled.
        if include_raw_answers:
            inner_answers = answers.get("answers") if "answers" in answers else answers
            if isinstance(inner_answers, dict):
                answers_raw["answers"] = dict(inner_answers)

    # Build flags block with derived info per CONTRACTS.md Level 4 (§16)
    flags_block: dict[str, Any] = {
        "emitted": sorted(flags_emitted),
        "final": sorted(final_flags),
        "derived": sorted(flags_derived) if flags_derived is not None else [],
    }

    # Build actions block with dedup info per CONTRACTS.md Level 4 (§16)
    actions_block: dict[str, Any] = {
        "selected": sorted(selected_action_ids),
        "deduped": actions_deduped if actions_deduped is not None else {},
    }

    trace: dict[str, Any] = {
        "answers_raw": answers_raw,
        "flags": flags_block,
        "rules_applied": rules_applied,
        "actions": actions_block,
        "articles_overlay": {k: sorted(v) for k, v in article_overlay.items()},
        "due_hints": due_hints,
    }

    # Add derivations_applied and rules_evaluated per CONTRACTS.md Level 4 (§16-17)
    trace["derivations_applied"] = derivations_applied if derivations_applied is not None else []
    trace["rules_evaluated"] = rules_evaluated if rules_evaluated is not None else 0

    # Add version info per CONTRACTS.md Level 4 (§16)
    if engine_version is not None:
        trace["engine_version"] = engine_version
    if contracts_version is not None:
        trace["contracts_version"] = contracts_version
    if templates_hash is not None:
        trace["templates_hash"] = templates_hash

    return trace
