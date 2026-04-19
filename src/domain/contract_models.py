# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pydantic models for Intrinsical Policy Engine contracts.

RUNTIME DOMAIN MODELS
=====================
These models provide type safety and validation at the domain boundary.
They are used in runtime to ensure contract invariants before data enters
the business logic.

Benefits:
- IDE autocompletion for all contract structures
- Fail-fast validation at load() time, not deep in business logic
- Eliminates defensive .get() and isinstance() checks in domain code
- Immutable, predictable data structures

Design:
- All models use extra="forbid" to reject unknown YAML fields (typo detection)
- Fields match actual YAML structure (not hypothetical ideal)
- Optional fields have sensible defaults
- Strict validation of enum-like fields where the engine owns the vocabulary
"""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from src.domain.types import ActionId, ArticleId, Flag, PackId

logger = logging.getLogger(__name__)


# =============================================================================
# BASE CONFIG - All models tolerate unknown fields
# =============================================================================


class _StrictModel(BaseModel):
    """Base model with validation for all contract models.

    Uses extra='forbid' to reject unknown YAML fields (typo detection),
    ensuring data integrity for critical compliance contracts.
    """

    # extra="forbid" detects typos (e.g. 'effor' instead of 'effort')
    # frozen=True ensures immutability for deterministic plan construction.
    model_config = ConfigDict(extra="forbid", frozen=True)


class EffortEstimate(_StrictModel):
    """
    Effort calibrated estimation based on Compliance Units (CU).
    1 CU ~= 1 efective work hour.
    """

    technical: int = Field(default=0, ge=0, description="Ingeniería, logs, seguridad")
    documentation: int = Field(default=0, ge=0, description="Redacción legal, procesos")
    external: int = Field(default=0, ge=0, description="Auditoría, notario, fees")


# =============================================================================
# FLAGS CONTRACT
# =============================================================================


class FlagDefinition(_StrictModel):
    """A compliance flag definition from flags.yml registry.

    Matches YAML structure:
        - id: role.source
          description: Source role
          set_by: [S1_Q8, derived:rules]
          type: boolean
          supersedes: [old.flag]
    """

    id: Flag = Field(min_length=1, description="Unique flag identifier")
    label: str | None = Field(default=None, description="Short display label")
    description: str = Field(default="", description="Human-readable description")
    set_by: list[str] = Field(
        default_factory=list, description="Questions/derivations that set this flag"
    )
    type: str | None = Field(default=None, description="Flag type (e.g., 'boolean')")
    supersedes: list[str] = Field(
        default_factory=list, description="Flags that this flag supersedes"
    )


class FlagsContract(_StrictModel):
    """Flags contract containing registry of all available flags."""

    version: str = Field(default="1.0.0", description="Contract version")
    governance: dict[str, Any] = Field(default_factory=dict, description="Governance metadata")
    registry: list[FlagDefinition] = Field(
        default_factory=list, description="List of flag definitions"
    )


# =============================================================================
# ACTIONS CONTRACT
# =============================================================================


class LegalSource(_StrictModel):
    """Legal source reference for traceability.

    Matches YAML structure:
        source:
            article: "Topic 5"
            paragraph: "section 1"
            eli: "https://example.invalid/framework/source/topic-5"
            notes: "Source-specific traceability note"
    """

    article: str = Field(default="", description="Topic reference (e.g., 'Topic 5')")
    paragraph: str | None = Field(default=None, description="Paragraph reference")
    section: str | None = Field(default=None, description="Section reference")
    eli: str | None = Field(default=None, description="ELI URI for legal traceability")
    notes: str | None = Field(default=None, description="Implementation notes")


class ActionDefinition(_StrictModel):
    """A compliance action definition from actions.yml.

    Matches YAML structure:
        - id: CTRL-4-LIT-POL
          title: Política de alfabetización en IA
          description: Detailed description of the action
          applies_to: any
          priority: medium
          when: has('role.source') or has('role.operator')
          legal_refs: ['Framework source: section 4']
          articles: [TOPIC-4]
          source: {article: "Art. 4", eli: "..."}
          evidence: [ai_literacy_policy.md]
          effort: {technical: 4, documentation: 2}
    """

    id: ActionId = Field(min_length=1, description="Unique action identifier")
    title: str = Field(min_length=1, description="Action title")
    description: str | None = Field(default=None, description="Detailed description of the action")
    applies_to: str | list[str] = Field(
        default="any", description="Target role(s): any or framework-defined role ids"
    )
    priority: str = Field(default="medium", description="Priority: critical, high, medium, low")
    when: dict[str, Any] | str | None = Field(
        default=None, description="Applicability condition (DSL)"
    )
    articles: list[ArticleId] = Field(default_factory=list, description="Related article IDs")
    legal_refs: list[str] = Field(default_factory=list, description="Framework references")
    evidence: list[str] = Field(default_factory=list, description="Required evidence files")
    source: LegalSource | None = Field(default=None, description="Legal source for traceability")
    type: str | None = Field(default=None, description="Action type (e.g., 'advisory')")
    related_actions: list[ActionId] = Field(default_factory=list, description="Related action IDs")
    category: str | None = Field(default=None, description="Action category (e.g., engineering)")
    effort_t_shirt: str | None = Field(default=None, description="Effort sizing hint (S/M/L/XL)")
    requires_evidence: bool | None = Field(
        default=None, description="Whether evidence is explicitly required"
    )

    # NUEVO: esfuerzo calibrado desde el framework (no inventado en código)
    effort: EffortEstimate | None = Field(
        default=None, description="Coste estimado calibrado por el framework (CU)"
    )

    @field_validator("priority")
    @classmethod
    def validate_priority(cls, v: str) -> str:
        """Validate and normalize priority level (strict - rejects invalid values)."""
        valid = {"critical", "high", "medium", "low"}
        normalized = str(v).lower().strip()
        if normalized not in valid:
            raise ValueError(f"Invalid priority '{v}'. Must be one of: {sorted(valid)}")
        return normalized

    @field_validator("applies_to")
    @classmethod
    def validate_applies_to(cls, v: str | list[str]) -> str | list[str]:
        """Normalize applies_to while allowing framework-defined roles."""
        if isinstance(v, list):
            normalized_list = []
            for item in v:
                norm = str(item).lower().strip()
                if not norm:
                    raise ValueError("Invalid applies_to entry: value must be non-empty")
                normalized_list.append(norm)
            return normalized_list

        normalized = str(v).lower().strip()
        if not normalized:
            raise ValueError("Invalid applies_to: value must be non-empty")
        return normalized


class ActionsContract(_StrictModel):
    """Actions contract from actions.yml."""

    version: str = Field(default="1.0.0", description="Contract version")
    schema_name: str | None = Field(default=None, description="Schema identifier")
    defaults: dict[str, Any] = Field(default_factory=dict, description="Default values for actions")
    actions: list[ActionDefinition] = Field(default_factory=list, description="List of actions")


# =============================================================================
# RULES CONTRACT - Derivations, Packs, Stops
# =============================================================================


class Derivation(_StrictModel):
    """A flag derivation rule from rules.yml.

    Matches YAML structure:
        - id: "DER-AR-REP"
          when:
            has: role.auth_rep_required
          set_flags: ["role.source"]
    """

    id: str | None = Field(default=None, description="Optional derivation identifier for tracing")
    when: dict[str, Any] | str | None = Field(default=None, description="Condition (DSL)")
    set_flags: list[Flag] = Field(
        default_factory=list, description="Flags to set when condition matches"
    )

    @property
    def flags_to_set(self) -> list[Flag]:
        """Return flags to set."""
        return self.set_flags


class Pack(_StrictModel):
    """A rule pack that groups multiple actions from rules.yml.

    Matches YAML structure:
        - id: "PACK-BLOCKED"
          when:
            any_prefix: blocked
          add_actions: ["A_STOP"]
          halt: true
    """

    id: PackId = Field(min_length=1, description="Unique pack identifier")
    when: dict[str, Any] | str = Field(description="Pack applicability condition (DSL)")
    add_actions: list[ActionId] = Field(default_factory=list, description="Action IDs to add")
    halt: bool = Field(default=False, description="Whether pack halts further processing")


class Stop(_StrictModel):
    """A stop rule from rules.yml that terminates evaluation.

    Matches YAML structure:
        - id: "STOP-TERRITORIAL"
          when:
            has: scope.geo.no_eu_nexus
          outcome: "out_of_scope_territorial"
    """

    id: str = Field(min_length=1, description="Stop rule identifier")
    when: dict[str, Any] | str = Field(description="Stop condition (DSL)")
    outcome: str = Field(description="Outcome when stop is triggered")


class RegulatoryMeta(_StrictModel):
    """Regulatory interpretation version metadata from rules.yml."""

    version: str = Field(default="1.0", description="Regulatory interpretation version")
    effective_date: str = Field(default="", description="ISO date (YYYY-MM-DD)")
    source: str = Field(default="", description="Legal basis")
    supersedes: list[str] = Field(default_factory=list, description="Previous versions replaced")
    rationale: str | None = Field(default=None, description="Detailed regulatory changes")
    reference_base_path: str | None = Field(default=None, description="Path to references")
    reference_files: list[str] = Field(default_factory=list, description="Reference filenames")


class RoutingRouterConfig(_StrictModel):
    """Configuration for selecting a framework-defined assessment route."""

    prefer_primary_if_flags: list[str] = Field(default_factory=list)
    prefer_primary_if_topics: list[str] = Field(default_factory=list)
    force_alternative_if_flags: list[str] = Field(default_factory=list)
    enforce: bool = Field(default=False)
    force_rationale: str | None = Field(default=None)


class HaltRule(_StrictModel):
    """A halt rule from rules.yml that stops processing without outcome."""

    id: str = Field(min_length=1, description="Halt rule identifier")
    when: dict[str, Any] | str = Field(description="Halt condition (DSL)")


class RulesContract(_StrictModel):
    """Rules contract from rules.yml - derivations, packs, stops, halt, and config."""

    version: str = Field(default="1.0.0", description="DSL version")
    schema_name: str | None = Field(default=None, description="Schema identifier")

    # Core rule structures
    derivations: list[Derivation] = Field(default_factory=list, description="Flag derivation rules")
    packs: list[Pack] = Field(default_factory=list, description="Action packs")
    stops: list[Stop] = Field(default_factory=list, description="Stop rules")
    halt: list[HaltRule] = Field(
        default_factory=list,
        description="Halt rules (stop without outcome)",
    )

    # Regulatory versioning and configuration
    regulatory_meta: RegulatoryMeta | None = Field(default=None)
    routing_router: RoutingRouterConfig | None = Field(default=None)
    risk_priorities: dict[str, int] = Field(default_factory=dict)

    # Dynamic classifiers (complex structure, kept as dict)
    classifiers: dict[str, Any] = Field(default_factory=dict, description="YAML-driven classifiers")


# =============================================================================
# ARTICLES CONTRACT
# =============================================================================


class ArticleDefinition(_StrictModel):
    """A framework article definition from articles.yml."""

    id: ArticleId = Field(min_length=1, description="Article identifier")
    title: str = Field(default="", description="Article title")
    description: str | None = Field(default=None, description="Optional description")
    scope: str | None = Field(default=None, description="Scope or applicability notes")
    related_actions: list[ActionId] = Field(
        default_factory=list, description="Actions related to this article"
    )
    cross_refs: list[str] = Field(default_factory=list, description="Cross-references")
    notes: list[str] = Field(default_factory=list, description="Additional notes")


class ArticlesContract(_StrictModel):
    """Articles contract containing article taxonomy."""

    version: str = Field(default="1.0.0", description="Contract version")
    taxonomy: list[ArticleDefinition] = Field(
        default_factory=list, description="Article definitions"
    )


# =============================================================================
# DUE RULES CONTRACT
# =============================================================================


class DueRuleEntry(_StrictModel):
    """A due date rule entry from due_rules.yml.

    Matches YAML structure:
        - prefixes: ["CTRL-50-"]
          calendar_keys: ["transparency"]
        - ids: ["CTRL-4-LIT-POL"]
          calendar_keys: ["ai_literacy_policy"]
        - policy: review
          prefixes: [...]
          calendar_keys: ["review_window_start"]
    """

    # Action matchers
    ids: list[str] = Field(default_factory=list, description="Action IDs to match")
    prefixes: list[str] = Field(default_factory=list, description="Action ID prefixes to match")
    policy: str | None = Field(default=None, description="Policy name (e.g., 'review')")
    priority: int | None = Field(
        default=None, description="Explicit rule priority (higher values win)"
    )

    # Calendar binding
    calendar_keys: list[str] = Field(
        default_factory=list, description="Calendar keys for deadline lookup"
    )


class DueRulesContract(_StrictModel):
    """Due rules contract from due_rules.yml."""

    version: str = Field(default="1.0.0", description="Contract version")
    rules: list[DueRuleEntry] = Field(default_factory=list, description="Due rule entries")
    policy_weights: dict[str, int] = Field(
        default_factory=dict, description="Policy weight overrides"
    )
    calendar_aliases: dict[str, list[str]] = Field(
        default_factory=dict, description="Calendar alias mappings"
    )


# Legacy alias
DueRule = DueRuleEntry


# =============================================================================
# DEDUPS CONTRACT
# =============================================================================


class DedupMapping(_StrictModel):
    """A deduplication mapping from dedups.yml.

    Matches YAML structure:
        - alias: "DECL-A5"
          canonical: "CTRL-47-DECL"
          rationale: "Ambas refieren a la Declaración UE"
    """

    alias: str = Field(description="Alias action ID to deduplicate")
    canonical: str = Field(description="Canonical action ID to keep")
    rationale: str = Field(default="", description="Reason for deduplication")


class DedupsContract(_StrictModel):
    """Dedups contract from dedups.yml."""

    version: str = Field(default="1.0.0", description="Contract version")
    mappings: list[DedupMapping] = Field(default_factory=list, description="Dedup mappings")


# =============================================================================
# CALENDAR CONTRACT
# =============================================================================


class CalendarContract(_StrictModel):
    """Law calendar from law_calendar.yml."""

    # Calendar needs extra="allow" because it's a dynamic key-value store
    # (event_name -> date), not a fixed schema
    model_config = ConfigDict(extra="allow", frozen=True)


# =============================================================================
# FRAMEWORK RUNTIME
# =============================================================================


class RuntimeProfileAttributeMatch(_StrictModel):
    """A single flag-to-attribute mapping rule for SubjectProfile construction."""

    flag: str | None = Field(default=None, description="Exact flag match")
    prefix: str | None = Field(default=None, description="Flag prefix match")
    value: Any = Field(default=None, description="Value to assign when the rule matches")


class RuntimeProfileAttributeRule(_StrictModel):
    """Runtime rule describing how to derive a subject attribute."""

    key: str = Field(min_length=1, description="Target SubjectProfile attribute key")
    mode: str = Field(
        default="first_match",
        description="Derivation mode: first_match, all_prefixes, or all_matches",
    )
    prefix: str | None = Field(default=None, description="Prefix used by all_prefixes mode")
    exclude: list[str] = Field(default_factory=list, description="Values or flags to exclude")
    matches: list[RuntimeProfileAttributeMatch] = Field(
        default_factory=list,
        description="Ordered match list for first_match/all_matches modes",
    )


class RuntimeSubjectProfile(_StrictModel):
    """Runtime configuration for generic SubjectProfile construction."""

    classification_key: str = Field(default="risk_tier")
    default_name: str = Field(default="Unnamed Subject")
    attributes: list[RuntimeProfileAttributeRule] = Field(default_factory=list)


class RuntimePredicateRule(_StrictModel):
    """Declarative predicate rule resolved from the active framework runtime."""

    predicate: str | None = Field(default=None)
    roles_any: list[str] = Field(default_factory=list)
    roles_all: list[str] = Field(default_factory=list)
    regimes_any: list[str] = Field(default_factory=list)
    tier_in: list[str] = Field(default_factory=list)
    flags_any: list[str] = Field(default_factory=list)
    flags_all: list[str] = Field(default_factory=list)
    outcomes_any: list[str] = Field(default_factory=list)
    attribute_equals: dict[str, Any] = Field(default_factory=dict)
    any_of: list[RuntimePredicateRule] = Field(default_factory=list)
    all_of: list[RuntimePredicateRule] = Field(default_factory=list)


class RuntimePredicateDefinition(_StrictModel):
    """Named predicate available to bundle profiles."""

    name: str = Field(min_length=1)
    rule: RuntimePredicateRule


class RuntimeRoutingPolicy(_StrictModel):
    """Routing policy owned by the framework pack."""

    review_tiers: list[str] = Field(default_factory=lambda: ["review"])
    impact_review_required_flags: list[str] = Field(
        default_factory=lambda: ["impact_review.required"]
    )
    preferred_route: str = Field(default="standard-review")
    alternative_route: str = Field(default="enhanced-review")
    impact_review_only_route: str = Field(default="impact_review-only")
    prefer_primary_if_flags: list[str] = Field(default_factory=list)
    prefer_primary_if_articles: list[str] = Field(default_factory=list)
    route_action_exclusions: dict[str, list[str]] = Field(default_factory=dict)


class RuntimeMetricsSignal(_StrictModel):
    """Signal definition for metrics computed from flags or article coverage."""

    when_flags: str | None = Field(default=None)
    articles_any: list[str] = Field(default_factory=list)
    score: float = Field(default=0.0)
    source: str = Field(default="runtime")


class RuntimeMetricsPolicy(_StrictModel):
    """Metrics hints controlled by the framework pack."""

    default_score: float = Field(default=0.10)
    default_source: str = Field(default="runtime_default")
    hitl_signals: list[RuntimeMetricsSignal] = Field(default_factory=list)
    article_signals: list[RuntimeMetricsSignal] = Field(default_factory=list)


class RuntimeDueDatePolicy(_StrictModel):
    """Due date policy overrides controlled by the framework pack."""

    policy_weights: dict[str, int] = Field(default_factory=dict)
    calendar_aliases: dict[str, list[str]] = Field(default_factory=dict)


class RuntimePolicies(_StrictModel):
    """Behavioral policies applied by the engine for the active framework."""

    subject_profile: RuntimeSubjectProfile = Field(default_factory=RuntimeSubjectProfile)
    routing: RuntimeRoutingPolicy = Field(default_factory=RuntimeRoutingPolicy)
    due_dates: RuntimeDueDatePolicy = Field(default_factory=RuntimeDueDatePolicy)
    metrics: RuntimeMetricsPolicy = Field(default_factory=RuntimeMetricsPolicy)
    predicates: list[RuntimePredicateDefinition] = Field(default_factory=list)


class FrameworkSemantics(_StrictModel):
    """Canonical ontology owned by the framework pack."""

    framework_id: str = Field(min_length=1)
    roles: list[str] = Field(default_factory=list)
    tiers: list[str] = Field(default_factory=list)
    regimes: list[str] = Field(default_factory=list)
    routes: list[str] = Field(default_factory=list)
    outcomes: list[str] = Field(default_factory=list)
    subject_attributes: list[str] = Field(default_factory=list)


class FrameworkPresentation(_StrictModel):
    """Branding and surface defaults supplied by the pack."""

    framework_name: str = Field(default="")
    engine_name: str = Field(default="intrinsical-policy-engine")
    cli_prog: str = Field(default="ipe")
    ics_prodid: str = Field(default="-//intrinsical-policy-engine//Compliance Calendar//EN")
    omission_roles: list[str] = Field(default_factory=list)


class FrameworkRuntime(_StrictModel):
    """Runtime configuration resolved from the active framework pack."""

    semantics: FrameworkSemantics
    policies: RuntimePolicies
    presentation: FrameworkPresentation


RuntimePredicateRule.model_rebuild()


# =============================================================================
# QUESTIONS CONTRACT
# =============================================================================


class QuestionOption(_StrictModel):
    """An answer option for a question."""

    value: str = Field(description="Option value")
    label: str = Field(default="", description="Display label")
    flags: list[Flag] = Field(default_factory=list, description="Flags set by this option")


class Question(_StrictModel):
    """A questionnaire question from questions.yml."""

    id: str = Field(description="Question identifier")
    text: str = Field(default="", description="Question text")
    type: str = Field(default="single", description="Question type: single, multi, text")
    options: list[QuestionOption] = Field(default_factory=list, description="Answer options")


# =============================================================================
# CONTRACT BUNDLE - Main runtime container
# =============================================================================


class ContractBundle(_StrictModel):
    """Complete contract bundle with all components.

    This is the main runtime container used throughout the domain.
    It provides typed access to all contract structures.

    Usage:
        bundle = ContractBundle.from_yaml_dicts(flags_dict, actions_dict, ...)
        for action in bundle.actions.actions:
            print(action.id, action.title)  # IDE autocompletion works!
    """

    # Core contracts
    flags: FlagsContract = Field(default_factory=FlagsContract)
    actions: ActionsContract = Field(default_factory=ActionsContract)
    rules: RulesContract = Field(default_factory=RulesContract)
    articles: ArticlesContract = Field(default_factory=ArticlesContract)
    due_rules: DueRulesContract = Field(default_factory=DueRulesContract)
    dedups: DedupsContract = Field(default_factory=DedupsContract)
    calendar: dict[str, Any] = Field(default_factory=dict)
    questions: dict[str, Any] = Field(default_factory=dict)
    risk_config: dict[str, Any] = Field(default_factory=dict)
    runtime: FrameworkRuntime = Field(
        default_factory=lambda: FrameworkRuntime(
            semantics=FrameworkSemantics(framework_id="unknown"),
            policies=RuntimePolicies(),
            presentation=FrameworkPresentation(),
        )
    )

    # Metadata
    version: str = Field(default="1.0.0", description="Bundle version")
    path: str = Field(default="", description="Bundle path")
    evidence_map: dict[str, Any] = Field(default_factory=dict)
    audit: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)

    @classmethod
    def from_yaml_dicts(
        cls,
        *,
        flags: dict | None = None,
        actions: dict | None = None,
        rules: dict | None = None,
        articles: dict | None = None,
        due_rules: dict | None = None,
        dedups: dict | None = None,
        calendar: dict | None = None,
        questions: dict | None = None,
        risk_config: dict | None = None,
        runtime: dict | None = None,
        version: str = "1.0.0",
        path: str = "",
        evidence_map: dict | None = None,
        audit: dict | None = None,
        metadata: dict | None = None,
    ) -> ContractBundle:
        """Create a ContractBundle from raw YAML dicts.

        This is the main entry point for parsing YAML into typed models.
        All parsing/validation happens here at load time.
        """
        semantics_dict: dict[str, Any] = {"framework_id": "unknown"}
        policies_dict: dict[str, Any] = {}
        presentation_dict: dict[str, Any] = {}
        if isinstance(runtime, dict):
            semantics = runtime.get("semantics")
            policies = runtime.get("policies")
            presentation = runtime.get("presentation")
            if isinstance(semantics, dict):
                semantics_dict.update(semantics)
            if isinstance(policies, dict):
                policies_dict = policies
            if isinstance(presentation, dict):
                presentation_dict = presentation

        actions_dict = dict(actions or {})
        actions_schema = actions_dict.pop("schema", None)
        if actions_schema is not None:
            actions_dict["schema_name"] = actions_schema

        rules_dict = dict(rules or {})
        rules_schema = rules_dict.pop("schema", None)
        if rules_schema is not None:
            rules_dict["schema_name"] = rules_schema

        return cls(
            flags=FlagsContract(**(flags or {})),
            actions=ActionsContract(**actions_dict),
            rules=RulesContract(**rules_dict),
            articles=ArticlesContract(**(articles or {})),
            due_rules=DueRulesContract(**(due_rules or {})),
            dedups=DedupsContract(**(dedups or {})),
            calendar=calendar or {},
            questions=questions or {},
            risk_config=risk_config or {},
            runtime=FrameworkRuntime(
                semantics=FrameworkSemantics(**semantics_dict),
                policies=RuntimePolicies(**policies_dict),
                presentation=FrameworkPresentation(**presentation_dict),
            ),
            version=version,
            path=path,
            evidence_map=evidence_map or {},
            audit=audit or {},
            metadata=metadata or {},
        )

    # -------------------------------------------------------------------------
    # Convenience accessors for common patterns
    # -------------------------------------------------------------------------

    def get_action_by_id(self, action_id: str) -> ActionDefinition | None:
        """Get action by ID, or None if not found."""
        for action in self.actions.actions:
            if action.id == action_id:
                return action
        return None

    def get_actions_dict(self) -> dict[str, ActionDefinition]:
        """Return dict mapping action ID to ActionDefinition."""
        return {a.id: a for a in self.actions.actions}

    @property
    def actions_list(self) -> list[ActionDefinition]:
        """Direct access to the actions list."""
        return self.actions.actions

    @property
    def derivations_list(self) -> list[Derivation]:
        """Direct access to the derivations list."""
        return self.rules.derivations

    @property
    def packs_list(self) -> list[Pack]:
        """Direct access to the packs list."""
        return self.rules.packs

    @property
    def stops_list(self) -> list[Stop]:
        """Direct access to the stops list."""
        return self.rules.stops

    # -------------------------------------------------------------------------


def validate_contract_dict(data: dict[str, Any], *, strict: bool = True) -> ContractBundle:
    """Validate and parse contract data from dictionary.

    Args:
        data: Raw contract data from YAML
        strict: If True, raise validation errors; if False, use defaults

    Returns:
        Validated ContractBundle instance

    Raises:
        ValidationError: If validation fails in strict mode
    """
    if strict:
        return ContractBundle(**data)
    else:
        try:
            return ContractBundle(**data)
        except (ValueError, TypeError, KeyError, ValidationError) as e:
            logger.warning(f"Contract validation failed (non-strict): {e}")
            # Return empty bundle on failure
            return ContractBundle()
