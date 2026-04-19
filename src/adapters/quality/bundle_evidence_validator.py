# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Bundle evidence validator for cross-referential integrity (INV-B2).

This validator ensures that BundleProfile trace_back_to references point to
real actions and evidences that exist in the ContractBundle.
"""

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

import src.domain.bundles.registry as bundle_registry
from src.domain.bundles.context import EvalContext
from src.domain.bundles.models import BundleNode, BundleProfile
from src.domain.bundles.registry import PredicateRegistry
from src.domain.contract_models import ContractBundle
from src.domain.core.subject_profile import subject_profile_from_dict

if TYPE_CHECKING:
    from src.domain.types import Plan

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
# ARTICLE ID NORMALIZATION
# ═══════════════════════════════════════════════════════════════════════════════


def normalize_article_id(article_id: str) -> str:
    """Normalize an article ID to canonical format.

    Handles various formats used in different parts of the system:
    - "TOPIC-9" -> "TOPIC-9" (already canonical)
    - "9" -> "TOPIC-9"
    - "t9" -> "TOPIC-9"
    - "topic9" -> "TOPIC-9"
    - "Topic 9" -> "TOPIC-9"
    - "SECTION-IV" -> "SECTION-IV" (already canonical)
    - "sec4" -> "SECTION-4"
    - "section4" -> "SECTION-4"

    Args:
        article_id: Article ID in any supported format.

    Returns:
        Normalized canonical ID (e.g., "TOPIC-9", "SECTION-IV").
    """
    if not article_id:
        return article_id

    normalized = article_id.strip().upper()

    # Already canonical
    if normalized.startswith("TOPIC-") or normalized.startswith("SECTION-"):
        return normalized

    # Section patterns: sec4, section4, sectionIV
    section_match = re.match(r"^SEC(?:TION)?[-_]?([IVX0-9]+)$", normalized, re.IGNORECASE)
    if section_match:
        return f"SECTION-{section_match.group(1)}"

    # Topic patterns: t9, topic9, Topic 9
    art_match = re.match(r"^T(?:OPIC)?[-_\s]*([0-9]+)$", normalized, re.IGNORECASE)
    if art_match:
        return f"TOPIC-{art_match.group(1)}"

    # Pure numeric
    if normalized.isdigit():
        return f"TOPIC-{normalized}"

    # Fallback: return original with TOPIC- prefix if not already prefixed
    if article_id.startswith(("TOPIC-", "SECTION-")):
        return article_id
    return f"TOPIC-{article_id}"


def find_in_evidence_map(
    article_id: str, evidence_map: dict[str, list] | None
) -> tuple[str | None, list]:
    """Find an article in the evidence_map, trying normalized variants.

    Args:
        article_id: Article ID to search for.
        evidence_map: The evidence map to search in.

    Returns:
        Tuple of (matched_key, evidences) or (None, []) if not found.
    """
    if not evidence_map:
        return None, []

    # Try exact match first
    if article_id in evidence_map:
        return article_id, evidence_map[article_id]

    # Try normalized version
    normalized = normalize_article_id(article_id)
    if normalized in evidence_map:
        return normalized, evidence_map[normalized]

    # Try without prefix
    for prefix in ("TOPIC-", "SECTION-"):
        if normalized.startswith(prefix):
            stripped = normalized[len(prefix) :]
            if stripped in evidence_map:
                return stripped, evidence_map[stripped]

    # Try lowercase variants
    for key in evidence_map:
        if normalize_article_id(key) == normalized:
            return key, evidence_map[key]

    return None, []


@dataclass
class ValidationReport:
    """Report of validation problems found during bundle evidence validation."""

    problems: list[str]

    def has_errors(self) -> bool:
        """Return True if any validation errors were found."""
        return len(self.problems) > 0

    def summary(self) -> str:
        """Return a summary string of validation problems."""
        if not self.problems:
            return "No validation issues"
        return f"{len(self.problems)} issues: " + "; ".join(self.problems[:3])


@dataclass
class CoverageReport:
    """Report of INV-B1 coverage analysis for critical actions and evidences."""

    required_actions: set[str]
    covered_actions: set[str]
    required_evidences: set[str]
    covered_evidences: set[str]
    active_profiles: list[str]

    @property
    def missing_actions(self) -> set[str]:
        """Actions that are required but not covered by any active bundle profile."""
        return self.required_actions - self.covered_actions

    @property
    def missing_evidences(self) -> set[str]:
        """Evidences that are required but not covered by any active bundle profile."""
        return self.required_evidences - self.covered_evidences

    def has_critical_gaps(self) -> bool:
        """Return True if any critical actions or evidences are missing coverage."""
        return len(self.missing_actions) > 0 or len(self.missing_evidences) > 0

    def summary(self) -> str:
        """Return a summary string of coverage gaps."""
        if not self.has_critical_gaps():
            return f"Coverage OK (active profiles: {len(self.active_profiles)})"

        gaps = []
        if self.missing_actions:
            sample_actions = list(self.missing_actions)[:3]
            gaps.append(f"{len(self.missing_actions)} missing actions (e.g. {sample_actions})")
        if self.missing_evidences:
            sample_evidences = list(self.missing_evidences)[:3]
            gaps.append(
                f"{len(self.missing_evidences)} missing evidences (e.g. {sample_evidences})"
            )

        return "; ".join(gaps)


class BundleEvidenceValidator:
    """Bundle evidence validator for cross-referential integrity (INV-B2).

    SCOPE: BundleProfile ↔ ContractBundle

    This validator ensures that `trace_back_to` references in BundleProfile
    nodes point to REAL actions and evidences defined in the ContractBundle.
    It does NOT check filesystem paths—that's handled by evidence_validator.

    Think of it as: "Does the Blueprint reference valid Contract IDs?"

    This validator implements INV-B2 from docs/invariants/ENGINE-ARCHITECTURE-v1.md:
    - No phantom evidence references
    - No references to non-existent actions
    - No references to non-existent evidence_map entries

    This is a pure domain service with no I/O dependencies.
    """

    def validate_integrity(
        self, bundle_profiles: dict[str, BundleProfile], contract_bundle: ContractBundle
    ) -> ValidationReport:
        """Validate all bundle profiles against the contract bundle.

        Args:
            bundle_profiles: Dict of profile_id -> BundleProfile
            contract_bundle: The loaded contract bundle with actions and evidence_map

        Returns:
            ValidationReport with any integrity violations found
        """
        problems: list[str] = []

        # Build reference sets from contract bundle
        action_ids = {a.id for a in contract_bundle.actions.actions}
        evidence_ids = set(contract_bundle.evidence_map.keys())

        # Validate each profile
        for profile in bundle_profiles.values():
            problems.extend(self._validate_profile_references(profile, action_ids, evidence_ids))

        return ValidationReport(problems)

    def _validate_profile_references(
        self, profile: BundleProfile, action_ids: set[str], evidence_ids: set[str]
    ) -> list[str]:
        """Validate references in a single profile."""
        problems: list[str] = []

        # Flatten all nodes in the profile (including nested children)
        all_nodes = self._flatten_nodes(profile.nodes)

        for node in all_nodes:
            if node.trace_back_to:
                problems.extend(
                    self._validate_node_trace_back_to(profile.id, node, action_ids, evidence_ids)
                )

        return problems

    def _flatten_nodes(self, nodes: list[BundleNode]) -> list[BundleNode]:
        """Recursively flatten nested nodes into a flat list."""
        result: list[BundleNode] = []

        for node in nodes:
            result.append(node)
            if node.children:
                result.extend(self._flatten_nodes(node.children))

        return result

    def _validate_node_trace_back_to(
        self, profile_id: str, node: BundleNode, action_ids: set[str], evidence_ids: set[str]
    ) -> list[str]:
        """Validate trace_back_to references in a single node."""
        problems: list[str] = []

        if not node.trace_back_to:
            return problems

        # Validate action references
        actions = node.trace_back_to.get("actions", [])
        if isinstance(actions, list):
            for action_id in actions:
                # Skip wildcard - it's a valid coverage shorthand, not a phantom ID
                if action_id == "*":
                    continue
                if isinstance(action_id, str) and action_id not in action_ids:
                    problems.append(
                        f"[INV-B2] Profile '{profile_id}', node '{node.id}': "
                        f"references unknown action '{action_id}'"
                    )

        # Validate evidence references
        evidences = node.trace_back_to.get("evidences", [])
        if isinstance(evidences, list):
            for evidence_id in evidences:
                if isinstance(evidence_id, str):
                    # Deprecation warning for legacy EV-* IDs (Fase 0)
                    # Canonical format is TOPIC-* (for example, TOPIC-47), not EV-*.
                    if evidence_id.startswith("EV-"):
                        logger.warning(
                            "bundle_evidence_validator.deprecated_ev_id",
                            extra={
                                "profile_id": profile_id,
                                "node_id": node.id,
                                "evidence_id": evidence_id,
                                "migration_hint": f"Legacy ID '{evidence_id}' used. "
                                f"Migrate to 'TOPIC-*' format (canonical evidence_map keys).",
                            },
                        )

                    if evidence_id not in evidence_ids:
                        problems.append(
                            f"[INV-B2] Profile '{profile_id}', node '{node.id}': "
                            f"references unknown evidence '{evidence_id}'"
                        )

        return problems

    def validate_coverage(
        self,
        bundle_profiles: dict[str, BundleProfile],
        contract_bundle: ContractBundle,
        plan: "Plan",
    ) -> CoverageReport:
        """Validate coverage of critical actions and evidences (INV-B1).

        Key insight: Only validate coverage against ACTIVE profiles for this specific Plan.
        A Low Risk plan should not be required to have Review bundle coverage.

        Args:
            bundle_profiles: All available bundle profiles
            contract_bundle: Contract bundle with actions and evidence definitions
            plan: The current plan (determines which profiles should be active)

        Returns:
            CoverageReport with gaps between required and covered actions/evidences
        """
        # Step 1: Determine which profiles are actually active for this plan
        active_profiles = self._get_active_profiles(bundle_profiles, plan, contract_bundle)

        # Step 2: Extract required actions/evidences from the plan
        required_actions = self._get_required_actions_from_plan(plan)
        required_evidences = self._get_required_evidences_from_plan(plan, contract_bundle)

        # Step 3: Calculate coverage from active profiles only
        covered_actions, covered_evidences, _ = self._calculate_coverage_from_profiles(
            active_profiles, plan, contract_bundle
        )

        # Only count coverage against what is actually required for this plan.
        # Otherwise, wildcard/core artifacts can inflate covered_* beyond required_*,
        # resulting in confusing (covered/required) ratios in client-facing docs.
        covered_actions = covered_actions & required_actions
        covered_evidences = covered_evidences & required_evidences

        return CoverageReport(
            required_actions=required_actions,
            covered_actions=covered_actions,
            required_evidences=required_evidences,
            covered_evidences=covered_evidences,
            active_profiles=[p.id for p in active_profiles],
        )

    def _get_active_profiles(
        self,
        bundle_profiles: dict[str, BundleProfile],
        plan: "Plan",
        contract_bundle: ContractBundle,
    ) -> list[BundleProfile]:
        """Determine which bundle profiles are active for this specific plan.

        This implements the matiz: only validate profiles that SHOULD be active
        for this plan's outcome.
        """
        eval_context = self._build_eval_context(plan, contract_bundle)

        active: list[BundleProfile] = []
        for profile in bundle_profiles.values():
            if self._profile_applies_to_plan(profile, eval_context, bundle_registry.PREDICATES):
                active.append(profile)

        return active

    def _build_eval_context(
        self, plan: "Plan", contract_bundle: ContractBundle | None = None
    ) -> EvalContext:
        """Create an EvalContext from plan data, normalizing types."""

        system_profile_data = plan.get("system_profile") or {}
        roles_raw = system_profile_data.get("roles")
        regimes_raw = system_profile_data.get("regimes")

        roles = [str(role) for role in roles_raw] if isinstance(roles_raw, list) else []
        regimes = [str(regime) for regime in regimes_raw] if isinstance(regimes_raw, list) else []
        system_profile = subject_profile_from_dict(
            {
                **system_profile_data,
                "roles": roles,
                "regimes": regimes,
            }
        )

        flags_raw = plan.get("flags", [])
        flag_map = (
            {str(flag): True for flag in flags_raw if isinstance(flag, str)}
            if isinstance(flags_raw, list)
            else {}
        )

        extras = {}
        runtime = plan.get("framework_runtime") if isinstance(plan, dict) else None
        if runtime is None and contract_bundle is not None:
            runtime = contract_bundle.runtime
        if runtime is not None:
            extras["runtime"] = runtime
        return EvalContext(plan=plan, system_profile=system_profile, flags=flag_map, extras=extras)

    def _profile_applies_to_plan(
        self,
        profile: BundleProfile,
        eval_context: EvalContext,
        registry: PredicateRegistry,
    ) -> bool:
        """Check if a profile's applies_if conditions are met by the current plan."""
        applies_if = getattr(profile, "applies_if", [])
        if not applies_if:
            return True  # No conditions means always applies

        # All predicates in applies_if must be true (AND logic)
        try:
            return registry.evaluate_all(applies_if, eval_context)
        except ValueError as exc:
            logger.error(
                "bundle_evidence_validator.unknown_predicate",
                extra={
                    "profile_id": profile.id,
                    "applies_if": applies_if,
                    "error": str(exc),
                },
            )
            return False
        except Exception:  # noqa: BLE001 - defensive guardrail over arbitrary predicates
            # Predicate evaluation failed - conservative: assume false
            return False

    def _get_required_actions_from_plan(self, plan: "Plan") -> set[str]:
        """Extract required actions from plan.

        An action is required if:
        - priority == "critical", or
        - requires_evidence == True.
        """
        required = set()

        actions_meta = plan.get("actions_meta", [])
        if not isinstance(actions_meta, list):
            return required

        for action in actions_meta:
            if not isinstance(action, dict):
                continue

            action_id = action.get("id")
            priority = action.get("priority", "medium")
            requires_evidence = action.get("requires_evidence", False)

            # Critical actions or actions that explicitly require evidence
            if action_id and (priority == "critical" or requires_evidence):
                required.add(action_id)

        return required

    def _get_required_evidences_from_plan(
        self,
        plan: "Plan",
        contract_bundle: ContractBundle,
    ) -> set[str]:
        """Extract evidences that are required for this plan's actions.

        Red Team Fix (Fase 2.1): Extended to consider evidence_map entries with
        `required: true` (default) vs `required: false`. An evidence is required if:
        1. It's linked to a critical action's article, AND
        2. The evidence_map entry has `required: true` (or omits the field, defaulting to true)

        Per INV-B1 (docs/invariants/ENGINE-ARCHITECTURE-v1.md): Critical actions
        must have corresponding evidence templates. This ensures the bundle
        coverage validation is accurate.
        """
        required = set()

        # Get required actions first
        required_actions = self._get_required_actions_from_plan(plan)

        # Map actions to their required evidences via articles
        actions_meta = plan.get("actions_meta", [])
        if not isinstance(actions_meta, list):
            return required

        for action in actions_meta:
            if not isinstance(action, dict):
                continue

            action_id = action.get("id")
            if action_id not in required_actions:
                continue

            # Get articles for this action
            articles = action.get("articles", [])
            if isinstance(articles, list):
                for article in articles:
                    if not isinstance(article, str):
                        continue
                    # Use normalized lookup to handle format variations
                    matched_key, evidences = find_in_evidence_map(
                        article, contract_bundle.evidence_map
                    )
                    if matched_key:
                        # This article has evidence requirements - use canonical ID
                        required.add(normalize_article_id(article))

        # Red Team Fix (Fase 2.1): Also include articles with required evidences
        # from evidence_map, even if not explicitly linked to current plan actions.
        # This catches articles marked as mandatory in the framework.
        for article_key, evidences in (contract_bundle.evidence_map or {}).items():
            has_required_evidence = self._has_required_evidence(evidences)
            if has_required_evidence and self._article_relevant_to_plan(article_key, plan):
                # Always use canonical format for consistency
                required.add(normalize_article_id(article_key))

        return required

    def _has_required_evidence(self, evidences: list) -> bool:
        """Check if any evidence in the list is required (not optional).

        Evidence entries can be:
        - str: path only (required=True by default)
        - dict: {path: ..., required: bool} where required defaults to True
        """
        if not isinstance(evidences, list):
            return False

        for ev in evidences:
            if isinstance(ev, str):
                # Plain string path = required by default
                return True
            elif isinstance(ev, dict) and ev.get("required", True):
                # Dict with explicit required field; default to True
                return True
        return False

    def _article_relevant_to_plan(self, article_key: str, plan: "Plan") -> bool:
        """Check if an article is relevant to the current plan based on outcome/flags.

        This is a conservative check - we assume an article is relevant if:
        1. It appears in any action's articles list in the plan, OR
        2. The plan outcome suggests review (where most articles apply)

        This prevents requiring evidences for articles not applicable to the plan.
        """
        # Normalize the article key for comparison
        normalized_key = normalize_article_id(article_key)

        # Check if article appears in any plan action
        actions_meta = plan.get("actions_meta", [])
        if isinstance(actions_meta, list):
            for action in actions_meta:
                if isinstance(action, dict):
                    articles = action.get("articles", [])
                    if isinstance(articles, list):
                        # Compare normalized versions to handle format variations
                        for art in articles:
                            if isinstance(art, str) and normalize_article_id(art) == normalized_key:
                                return True

        # Also check articles_overlay for explicit article selection
        articles_overlay = plan.get("articles_overlay", {})
        if isinstance(articles_overlay, dict):
            # Check both original key and normalized version
            if article_key in articles_overlay:
                return True
            # Also check if any key in overlay normalizes to same value
            for overlay_key in articles_overlay:
                if normalize_article_id(overlay_key) == normalized_key:
                    return True

        return False

    def _calculate_coverage_from_profiles(
        self,
        active_profiles: list[BundleProfile],
        plan: "Plan",
        contract_bundle: ContractBundle | None = None,
    ) -> tuple[set[str], set[str], bool]:
        """Calculate which actions and evidences active profiles cover.

        Args:
            active_profiles: List of bundle profiles that apply to this plan
            plan: The plan being validated (for wildcard resolution)

        Returns:
            Tuple of (covered_actions, covered_evidences, has_wildcard_coverage)
            has_wildcard_coverage is True if any node declares ["*"] for actions
        """
        covered_actions: set[str] = set()
        covered_evidences: set[str] = set()
        has_wildcard_coverage = False

        # Build context for predicate evaluation
        eval_ctx = self._build_eval_context(plan, contract_bundle)

        for profile in active_profiles:
            # PHASE 2 FIX: Use recursive traversal to check predicates hierarchically.
            # If a parent dir is disabled, its children are skipped.
            active_nodes = self._collect_active_nodes(
                profile.nodes, eval_ctx, bundle_registry.PREDICATES
            )

            for node in active_nodes:
                # Skip nodes that are excluded from coverage metrics (e.g., technical artifacts)
                # INV-B1 fix: wildcards in backlog.csv/summary.json don't inflate coverage
                if not getattr(node, "counts_for_coverage", True):
                    continue

                trace_back = getattr(node, "trace_back_to", None)
                if trace_back:
                    actions = self._as_str_list(trace_back.get("actions"))

                    # Wildcard "*" means "covers all actions in plan"
                    # Per docs/invariants/ENGINE-ARCHITECTURE-v1.md INV-B1:
                    # core artifacts implicitly cover all actions.
                    if "*" in actions:
                        has_wildcard_coverage = True
                        # Expand wildcard to all plan actions
                        for action in plan.get("actions_meta", []):
                            if isinstance(action, dict) and action.get("id"):
                                covered_actions.add(action["id"])
                    else:
                        covered_actions.update(actions)

                    evidences = self._as_str_list(trace_back.get("evidences"))
                    if evidences:
                        covered_evidences.update(evidences)

        return covered_actions, covered_evidences, has_wildcard_coverage

    def _collect_active_nodes(
        self,
        nodes: list[BundleNode],
        ctx: EvalContext,
        registry: PredicateRegistry,
    ) -> list[BundleNode]:
        """Recursively collect nodes whose predicates (and parent predicates) evaluate to True."""
        active: list[BundleNode] = []
        for node in nodes:
            # Check node predicates
            if not self._evaluate_node_predicates(node, ctx, registry):
                continue

            active.append(node)
            if node.children:
                # Recurse only if parent is active
                active.extend(self._collect_active_nodes(node.children, ctx, registry))
        return active

    def _evaluate_node_predicates(
        self, node: BundleNode, ctx: EvalContext, registry: PredicateRegistry
    ) -> bool:
        """Return True if node predicates match."""
        if not node.predicates:
            return True
        try:
            return registry.evaluate_all(node.predicates, ctx)
        except Exception:  # noqa: BLE001
            return False

    @staticmethod
    def _as_str_list(value: object) -> list[str]:
        if isinstance(value, list):
            return [str(item) for item in value if isinstance(item, str)]
        return []
