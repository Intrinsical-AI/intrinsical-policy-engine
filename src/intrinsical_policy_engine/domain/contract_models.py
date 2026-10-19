# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Complete strict models for version three framework packs."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Annotated, Literal

from packaging.specifiers import SpecifierSet
from packaging.version import Version
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from intrinsical_policy_engine.domain.conditions import Condition, Identifier

Strings = Annotated[tuple[Identifier, ...], Field(strict=False)]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True)


class FlagDefinition(ContractModel):
    id: Identifier
    label: str | None = None
    description: str = ""


class FlagsContract(ContractModel):
    registry: tuple[FlagDefinition, ...] = Field(default=(), strict=False)


class EffortEstimate(ContractModel):
    technical: int = Field(default=0, ge=0)
    documentation: int = Field(default=0, ge=0)
    external: int = Field(default=0, ge=0)


class LegalSource(ContractModel):
    article: str = ""
    paragraph: str | None = None
    section: str | None = None
    eli: str | None = None
    notes: str | None = None


class ActionDefinition(ContractModel):
    id: Identifier
    title: str = Field(min_length=1)
    description: str | None = None
    applies_to: Literal["any"] | Strings = "any"
    priority: Literal["critical", "high", "medium", "low"] = "medium"
    when: Condition = Field(default_factory=Condition.true)
    articles: Strings = ()
    legal_refs: tuple[str, ...] = Field(default=(), strict=False)
    evidence: Strings = ()
    source: LegalSource | None = None
    category: str | None = None
    requires_evidence: bool = True
    effort: EffortEstimate | None = None


class ActionsContract(ContractModel):
    actions: tuple[ActionDefinition, ...] = Field(default=(), strict=False)


class Derivation(ContractModel):
    id: Identifier
    when: Condition = Field(default_factory=Condition.true)
    set_flags: Strings = Field(min_length=1)

    @field_validator("when")
    @classmethod
    def positive_only(cls, condition: Condition) -> Condition:
        if not condition.positive():
            raise ValueError("derivations allow only positive conditions; not is forbidden")
        return condition


class Stop(ContractModel):
    id: Identifier
    when: Condition
    outcome: Identifier


class RegulatoryMeta(ContractModel):
    version: Identifier
    effective_date: str | None = None
    source: str
    rationale: str | None = None

    @field_validator("effective_date", mode="before")
    @classmethod
    def date_valid(cls, value: object) -> str | None:
        return None if value is None else canonical_date(value)


class RoleMapping(ContractModel):
    id: Identifier
    when: Condition
    roles: Strings


class TierOutput(ContractModel):
    tier: Identifier
    regimes: Strings = ()


class RiskTierRule(ContractModel):
    id: Identifier
    priority: int = 0
    when: Condition
    output: TierOutput


class OutcomeRule(ContractModel):
    id: Identifier
    when: Condition
    outcome: Identifier


class ClassifiersContract(ContractModel):
    role_mapping: tuple[RoleMapping, ...] = Field(default=(), strict=False)
    risk_tiers: tuple[RiskTierRule, ...] = Field(default=(), strict=False)
    outcome_rules: tuple[OutcomeRule, ...] = Field(default=(), strict=False)


class RulesContract(ContractModel):
    derivations: tuple[Derivation, ...] = Field(default=(), strict=False)
    stops: tuple[Stop, ...] = Field(default=(), strict=False)
    classifiers: ClassifiersContract = Field(default_factory=ClassifiersContract)
    regulatory_meta: RegulatoryMeta | None = None


class ArticleDefinition(ContractModel):
    id: Identifier
    title: str = ""
    description: str | None = None
    related_actions: Strings = ()
    cross_refs: Strings = ()


class ArticlesContract(ContractModel):
    taxonomy: tuple[ArticleDefinition, ...] = Field(default=(), strict=False)


class DueRuleEntry(ContractModel):
    id: Identifier
    ids: Strings = ()
    prefixes: Strings = ()
    priority: int = 0
    calendar_keys: Strings = Field(min_length=1)

    @model_validator(mode="after")
    def require_selector(self) -> DueRuleEntry:
        if not self.ids and not self.prefixes:
            raise ValueError("due rule needs ids or prefixes")
        return self


class DueRulesContract(ContractModel):
    rules: tuple[DueRuleEntry, ...] = Field(default=(), strict=False)


def canonical_date(value: object, *, relative: bool = False) -> str:
    if isinstance(value, datetime):
        raise ValueError("calendar dates cannot contain a time")
    if isinstance(value, date):
        return value.isoformat()
    if not isinstance(value, str):
        raise ValueError("date must be an ISO date or YAML date scalar")
    match = re.fullmatch(r"T\+([0-9]+)([dm])", value) if relative else None
    if match is not None:
        # Compare bounded decimal strings before int(): authoring input can
        # exceed Python's integer conversion limit as well as date's range.
        digits = match.group(1).lstrip("0") or "0"
        unit = match.group(2)
        limit = str((date.max - date.min).days if unit == "d" else 9999 * 12 - 1)
        if len(digits) > len(limit) or (len(digits) == len(limit) and digits > limit):
            raise ValueError("relative calendar date exceeds the supported date range")
        return f"T+{digits}{unit}"
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        raise ValueError("date must use YYYY-MM-DD or T+Nd/T+Nm")
    return date.fromisoformat(value).isoformat()


class CalendarContract(ContractModel):
    calendar: dict[Identifier, str] = Field(default_factory=dict)
    base_date: str | None = None

    @field_validator("calendar", mode="before")
    @classmethod
    def dates(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        return {key: canonical_date(item, relative=True) for key, item in value.items()}

    @field_validator("base_date", mode="before")
    @classmethod
    def base(cls, value: object) -> str | None:
        return None if value is None else canonical_date(value)


class QuestionOption(ContractModel):
    value: Identifier
    label: str = Field(min_length=1)


class Question(ContractModel):
    id: Identifier
    text: str = Field(min_length=1)
    type: Literal["one_of", "multi", "text"]
    when: Condition = Field(default_factory=Condition.true)
    options: tuple[QuestionOption, ...] = Field(default=(), strict=False)
    map_to_flags: dict[str, Strings] = Field(default_factory=dict)

    @model_validator(mode="after")
    def mappings(self) -> Question:
        values = [option.value for option in self.options]
        if len(values) != len(set(values)):
            raise ValueError("question option values must be unique")
        if self.type == "text":
            if self.options or self.map_to_flags:
                raise ValueError("text questions cannot declare options or flag mappings")
        else:
            if not values:
                raise ValueError("selection questions need explicit options")
            if set(self.map_to_flags) - set(values):
                raise ValueError("map_to_flags contains unknown option values")
        return self


class QuestionGroup(ContractModel):
    id: Identifier
    title: str = ""
    questions: tuple[Question, ...] = Field(default=(), strict=False)


class QuestionnaireContract(ContractModel):
    groups: tuple[QuestionGroup, ...] = Field(default=(), strict=False)


class RuntimeSubjectProfile(ContractModel):
    default_name: str = "Unnamed Subject"


class RoutingRule(ContractModel):
    id: Identifier
    when: Condition
    route: Identifier
    exclude_actions: Strings = ()


class RuntimeRoutingPolicy(ContractModel):
    default_route: Identifier | None = None
    rules: tuple[RoutingRule, ...] = Field(default=(), strict=False)


class RuntimeMetricsSignal(ContractModel):
    id: Identifier
    when: Condition
    score: float = Field(ge=0, le=1)
    source: Identifier


class RuntimeMetricsPolicy(ContractModel):
    default_score: float = Field(default=0.1, ge=0, le=1)
    default_source: Identifier = "pack"
    signals: tuple[RuntimeMetricsSignal, ...] = Field(default=(), strict=False)


class RuntimePolicies(ContractModel):
    subject_profile: RuntimeSubjectProfile = Field(default_factory=RuntimeSubjectProfile)
    routing: RuntimeRoutingPolicy = Field(default_factory=RuntimeRoutingPolicy)
    metrics: RuntimeMetricsPolicy = Field(default_factory=RuntimeMetricsPolicy)


class FrameworkSemantics(ContractModel):
    framework_id: Identifier
    roles: Strings = ()
    tiers: Strings = ()
    regimes: Strings = ()
    routes: Strings = ()
    outcomes: Strings = ()
    blocked_outcomes: Strings = ()


class FrameworkPresentation(ContractModel):
    framework_name: str = ""
    ics_prodid: str = "-//intrinsical-policy-engine//Compliance Calendar//EN"


class FrameworkRuntime(ContractModel):
    semantics: FrameworkSemantics
    policies: RuntimePolicies = Field(default_factory=RuntimePolicies)
    presentation: FrameworkPresentation = Field(default_factory=FrameworkPresentation)


class EvidenceRequirement(ContractModel):
    path: Identifier


class ManifestContracts(ContractModel):
    flags: Strings
    actions: Strings
    rules: Strings
    articles: Strings
    questions: Strings = ()
    evidence_map: Strings = ()
    due_rules: Strings = ()
    calendar: Strings = ()


class ManifestRuntime(ContractModel):
    semantics: Strings
    policies: Strings
    presentation: Strings


class ManifestRuntimeFiles(ContractModel):
    context_defaults: Identifier
    backlog_config: Identifier


class PackManifest(ContractModel):
    pack_format_version: Literal[3]
    framework_id: Identifier
    version: Identifier
    requires_engine: Identifier
    contracts: ManifestContracts
    runtime: ManifestRuntime
    bundle_profiles: Strings = ()
    templates_dir: Identifier
    evidence_templates_dir: Identifier
    runtime_files: ManifestRuntimeFiles

    @field_validator("version")
    @classmethod
    def version_valid(cls, value: str) -> str:
        Version(value)
        return value

    @field_validator("requires_engine")
    @classmethod
    def engine_requirement(cls, value: str) -> str:
        SpecifierSet(value)
        return value


class ContractBundle(ContractModel):
    flags: FlagsContract = Field(default_factory=FlagsContract)
    actions: ActionsContract = Field(default_factory=ActionsContract)
    rules: RulesContract = Field(default_factory=RulesContract)
    articles: ArticlesContract = Field(default_factory=ArticlesContract)
    due_rules: DueRulesContract = Field(default_factory=DueRulesContract)
    calendar: CalendarContract = Field(default_factory=CalendarContract)
    questions: QuestionnaireContract = Field(default_factory=QuestionnaireContract)
    runtime: FrameworkRuntime
    path: str = ""
    evidence_map: dict[str, Annotated[tuple[EvidenceRequirement, ...], Field(strict=False)]] = (
        Field(default_factory=dict)
    )
    manifest: PackManifest

    def get_actions_dict(self) -> dict[str, ActionDefinition]:
        return {action.id: action for action in self.actions.actions}

    def get_action_by_id(self, action_id: str) -> ActionDefinition | None:
        return self.get_actions_dict().get(action_id)
