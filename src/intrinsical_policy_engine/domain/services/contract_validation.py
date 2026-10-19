# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Shared domain invariants and diagnostics for YAML and programmatic packs."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date
from pathlib import PurePosixPath
from typing import Literal

from packaging.specifiers import SpecifierSet
from packaging.version import Version

from intrinsical_policy_engine.common.constants import CANONICAL_ENGINE_VERSION
from intrinsical_policy_engine.domain.bundles.models import BundleNode, BundleProfile
from intrinsical_policy_engine.domain.conditions import (
    Condition,
    condition_references,
    evaluate_condition,
    namespace_matches,
)
from intrinsical_policy_engine.domain.contract_models import ContractBundle, PackManifest
from intrinsical_policy_engine.domain.output_inventory import (
    OutputCollisionError,
    OutputInventory,
    require_authored_path,
)
from intrinsical_policy_engine.domain.services.duedate_service import resolve_calendar_date
from intrinsical_policy_engine.domain.snapshot_contract import canonical_path


@dataclass(frozen=True)
class Problem:
    code: str
    severity: Literal["error", "warning"]
    source: str
    msg: str
    field_path: str = ""

    def to_dict(self) -> dict[str, str]:
        return {
            "code": self.code,
            "severity": self.severity,
            "source": self.source,
            "field_path": self.field_path,
            "message": self.msg,
        }


def ensure_engine_compatible(manifest: PackManifest) -> None:
    """Reject a syntactically valid requirement that excludes this engine."""
    if Version(CANONICAL_ENGINE_VERSION) not in SpecifierSet(manifest.requires_engine):
        raise ValueError(
            f"engine {CANONICAL_ENGINE_VERSION} does not satisfy "
            f"requires_engine {manifest.requires_engine}"
        )


class _BundleValidator:
    def __init__(self, bundle: ContractBundle, profiles: dict[str, BundleProfile]) -> None:
        self.bundle = bundle
        self.profiles = profiles
        self.problems: list[Problem] = []
        self.flags = {flag.id for flag in bundle.flags.registry}
        self.actions = {action.id for action in bundle.actions.actions}
        self.articles = {article.id for article in bundle.articles.taxonomy}
        self.semantics = bundle.runtime.semantics

    def error(self, code: str, where: str, message: str) -> None:
        self.problems.append(Problem(code, "error", "", message, field_path=where))

    def path(self, value: str, where: str) -> None:
        try:
            canonical_path(value)
        except ValueError as exc:
            self.error("CONTRACT-PATH", where, str(exc))

    def unique(self, values: Iterable[str], where: str) -> None:
        seen: set[str] = set()
        for value in values:
            if value in seen:
                self.error("DUPLICATE-ID", where, f"duplicate id: {value}")
            seen.add(value)

    def refs(self, values: Iterable[str], known: set[str], where: str, kind: str) -> None:
        for value in values:
            if value not in known:
                self.error("UNKNOWN-REFERENCE", where, f"unknown {kind}: {value}")

    def condition(self, value: Condition, where: str) -> None:
        exact, prefixes = condition_references(value)
        self.refs(sorted(exact), self.flags, where, "flag")
        for prefix in sorted(prefixes):
            if not any(namespace_matches(flag, prefix) for flag in self.flags):
                self.error("UNKNOWN-PREFIX", where, f"prefix matches no registered flags: {prefix}")

    def validate_semantics(self) -> None:
        try:
            ensure_engine_compatible(self.bundle.manifest)
        except ValueError as exc:
            self.error("ENGINE-COMPATIBILITY", "manifest.requires_engine", str(exc))
        if self.semantics.framework_id != self.bundle.manifest.framework_id:
            self.error(
                "FRAMEWORK-ID", "runtime.semantics.framework_id", "framework_id must match manifest"
            )
        for field in ("roles", "tiers", "regimes", "routes", "outcomes", "blocked_outcomes"):
            self.unique(getattr(self.semantics, field), f"runtime.semantics.{field}")
        self.refs(
            self.semantics.blocked_outcomes,
            set(self.semantics.outcomes),
            "runtime.semantics.blocked_outcomes",
            "outcome",
        )

    def validate_catalog(self) -> None:
        for section, ids in (
            ("flags.registry", (f.id for f in self.bundle.flags.registry)),
            ("actions.actions", (a.id for a in self.bundle.actions.actions)),
            ("articles.taxonomy", (a.id for a in self.bundle.articles.taxonomy)),
        ):
            self.unique(ids, section)
        for index, action in enumerate(self.bundle.actions.actions):
            where = f"actions.actions[{index}]"
            self.condition(action.when, where + ".when")
            self.refs(action.articles, self.articles, where + ".articles", "article")
            for evidence_index, evidence in enumerate(action.evidence):
                self.path(evidence, where + f".evidence[{evidence_index}]")
            if action.applies_to != "any":
                self.refs(
                    action.applies_to, set(self.semantics.roles), where + ".applies_to", "role"
                )
        for index, article in enumerate(self.bundle.articles.taxonomy):
            where = f"articles.taxonomy[{index}]"
            self.refs(article.related_actions, self.actions, where + ".related_actions", "action")
            self.refs(article.cross_refs, self.articles, where + ".cross_refs", "article")

    def validate_rules(self) -> None:
        for field in ("derivations", "stops"):
            rules = getattr(self.bundle.rules, field)
            self.unique((rule.id for rule in rules), f"rules.{field}")
            for index, rule in enumerate(rules):
                self.condition(rule.when, f"rules.{field}[{index}].when")
        for index, rule in enumerate(self.bundle.rules.derivations):
            self.refs(rule.set_flags, self.flags, f"rules.derivations[{index}].set_flags", "flag")
        for index, rule in enumerate(self.bundle.rules.stops):
            self.refs(
                [rule.outcome],
                set(self.semantics.outcomes),
                f"rules.stops[{index}].outcome",
                "outcome",
            )

    def validate_classifiers(self) -> None:
        classifiers = self.bundle.rules.classifiers
        for field in ("role_mapping", "risk_tiers", "outcome_rules"):
            rules = getattr(classifiers, field)
            self.unique((rule.id for rule in rules), f"rules.classifiers.{field}")
            for index, rule in enumerate(rules):
                self.condition(rule.when, f"rules.classifiers.{field}[{index}].when")
        for index, role_rule in enumerate(classifiers.role_mapping):
            self.refs(
                role_rule.roles,
                set(self.semantics.roles),
                f"rules.classifiers.role_mapping[{index}].roles",
                "role",
            )
        for index, tier_rule in enumerate(classifiers.risk_tiers):
            where = f"rules.classifiers.risk_tiers[{index}].output"
            self.refs([tier_rule.output.tier], set(self.semantics.tiers), where + ".tier", "tier")
            self.refs(
                tier_rule.output.regimes, set(self.semantics.regimes), where + ".regimes", "regime"
            )
        for index, outcome_rule in enumerate(classifiers.outcome_rules):
            self.refs(
                [outcome_rule.outcome],
                set(self.semantics.outcomes),
                f"rules.classifiers.outcome_rules[{index}].outcome",
                "outcome",
            )

    def validate_questions(self) -> None:
        self.unique((g.id for g in self.bundle.questions.groups), "questions.groups")
        self.unique(
            (q.id for group in self.bundle.questions.groups for q in group.questions),
            "questions.questions",
        )
        earlier_flags: set[str] = set()
        for group_index, group in enumerate(self.bundle.questions.groups):
            for index, question in enumerate(group.questions):
                where = f"questions.groups[{group_index}].questions[{index}]"
                self.condition(question.when, where + ".when")
                exact, prefixes = condition_references(question.when)
                known = exact <= self.flags and all(
                    any(namespace_matches(flag, prefix) for flag in self.flags)
                    for prefix in prefixes
                )
                # This is an upper bound: earlier option emissions need not be
                # jointly possible. Only warn if even that bound cannot match.
                if (
                    known
                    and (exact or prefixes)
                    and question.when.positive()
                    and not evaluate_condition(question.when, earlier_flags)
                ):
                    self.problems.append(
                        Problem(
                            "QUESTION-UNREACHABLE",
                            "warning",
                            "",
                            "Positive question condition cannot match flags emitted by earlier "
                            "questions; derivations run after the questionnaire",
                            field_path=where + ".when",
                        )
                    )
                for option, targets in question.map_to_flags.items():
                    self.refs(targets, self.flags, where + f".map_to_flags.{option}", "flag")
                    earlier_flags.update(targets)

    def validate_calendar(self) -> None:
        calendar = self.bundle.calendar
        if calendar.base_date is None:
            return
        base = date.fromisoformat(calendar.base_date)
        for key, value in calendar.calendar.items():
            try:
                resolve_calendar_date(value, base)
            except ValueError as exc:
                self.error("CALENDAR-RANGE", f"calendar.calendar.{key}", str(exc))

    def validate_due_rules(self) -> None:
        self.unique((rule.id for rule in self.bundle.due_rules.rules), "due_rules.rules")
        for index, rule in enumerate(self.bundle.due_rules.rules):
            where = f"due_rules.rules[{index}]"
            self.refs(rule.ids, self.actions, where + ".ids", "action")
            self.refs(
                rule.calendar_keys,
                set(self.bundle.calendar.calendar),
                where + ".calendar_keys",
                "calendar key",
            )
            for prefix in rule.prefixes:
                if not any(action.startswith(prefix) for action in self.actions):
                    self.error(
                        "UNKNOWN-PREFIX",
                        where + ".prefixes",
                        f"prefix matches no actions: {prefix}",
                    )

    def validate_evidence(self) -> None:
        for key, requirements in self.bundle.evidence_map.items():
            self.refs([key], self.actions | self.articles, f"evidence_map.{key}", "article/action")
            self.unique((requirement.path for requirement in requirements), f"evidence_map.{key}")
            for index, requirement in enumerate(requirements):
                self.path(requirement.path, f"evidence_map.{key}[{index}].path")

    def validate_policies(self) -> None:
        routing = self.bundle.runtime.policies.routing
        if routing.default_route is not None:
            self.refs(
                [routing.default_route],
                set(self.semantics.routes),
                "runtime.policies.routing.default_route",
                "route",
            )
        self.unique((rule.id for rule in routing.rules), "runtime.policies.routing.rules")
        for index, rule in enumerate(routing.rules):
            where = f"runtime.policies.routing.rules[{index}]"
            self.condition(rule.when, where + ".when")
            self.refs([rule.route], set(self.semantics.routes), where + ".route", "route")
            self.refs(rule.exclude_actions, self.actions, where + ".exclude_actions", "action")
        metrics = self.bundle.runtime.policies.metrics
        self.unique((signal.id for signal in metrics.signals), "runtime.policies.metrics.signals")
        for index, signal in enumerate(metrics.signals):
            self.condition(signal.when, f"runtime.policies.metrics.signals[{index}].when")

    def validate_nodes(self, nodes: tuple[BundleNode, ...], where: str) -> list[str]:
        node_ids: list[str] = []
        for index, node in enumerate(nodes):
            node_ids.append(node.id)
            loc = f"{where}[{index}]"
            self.condition(node.when, loc + ".when")
            self.refs(
                node.trace_back_to.actions, self.actions, loc + ".trace_back_to.actions", "action"
            )
            self.refs(
                node.trace_back_to.evidences,
                self.actions | self.articles,
                loc + ".trace_back_to.evidences",
                "evidence owner",
            )
            node_ids.extend(self.validate_nodes(node.children, loc + ".children"))
        return node_ids

    def validate_profiles(self) -> None:
        node_ids: list[str] = []
        inventory = OutputInventory()
        for profile in self.profiles.values():
            self.condition(profile.when, f"bundle_profiles.{profile.id}.when")
            node_ids.extend(
                self.validate_nodes(profile.nodes, f"bundle_profiles.{profile.id}.nodes")
            )
            self.validate_output_nodes(
                profile.nodes,
                PurePosixPath(profile.root_dir),
                f"bundle_profiles.{profile.id}.nodes",
                profile.when == Condition.true(),
                inventory,
            )
        self.unique(node_ids, "bundle_profiles.nodes")

    def validate_output_nodes(
        self,
        nodes: tuple[BundleNode, ...],
        parent: PurePosixPath,
        where: str,
        ancestors_always_active: bool,
        inventory: OutputInventory,
    ) -> None:
        for index, node in enumerate(nodes):
            field = "target" if node.kind == "copy" else "name"
            part = node.target if node.kind == "copy" else node.name
            if part is None:
                continue  # Node shape validation reports the missing destination.
            path = parent / part
            location = f"{where}[{index}]"
            always_active = ancestors_always_active and node.when == Condition.true()
            try:
                # Reserved paths are invalid even if a node is not always active.
                require_authored_path(path.as_posix())
                if always_active:
                    owner = f"{location} ({node.id})"
                    if node.kind == "dir":
                        inventory.add_directory(path.as_posix(), owner)
                    else:
                        inventory.add_file(path.as_posix(), owner)
            except OutputCollisionError as exc:
                self.error("OUTPUT-COLLISION", f"{location}.{field}", str(exc))
            except ValueError as exc:
                self.error("CONTRACT-PATH", f"{location}.{field}", str(exc))
            self.validate_output_nodes(
                node.children, path, location + ".children", always_active, inventory
            )


def validate_bundle(bundle: ContractBundle, profiles: dict[str, BundleProfile]) -> list[Problem]:
    validator = _BundleValidator(bundle, profiles)
    validator.validate_semantics()
    validator.validate_catalog()
    validator.validate_rules()
    validator.validate_classifiers()
    validator.validate_questions()
    validator.validate_calendar()
    validator.validate_due_rules()
    validator.validate_evidence()
    validator.validate_policies()
    validator.validate_profiles()
    return validator.problems
