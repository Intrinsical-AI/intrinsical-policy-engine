# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Coverage uses the same guarded profile traversal as rendering."""

from dataclasses import dataclass, field
from typing import Any

from intrinsical_policy_engine.domain.bundles.models import BundleProfile
from intrinsical_policy_engine.domain.bundles.selection import selected_nodes


@dataclass(frozen=True)
class CoverageReport:
    required_actions: set[str] = field(default_factory=set)
    covered_actions: set[str] = field(default_factory=set)
    required_evidences: set[str] = field(default_factory=set)
    covered_evidences: set[str] = field(default_factory=set)
    active_profiles: list[str] = field(default_factory=list)

    @property
    def missing_actions(self) -> set[str]:
        return self.required_actions - self.covered_actions

    @property
    def missing_evidences(self) -> set[str]:
        return self.required_evidences - self.covered_evidences

    def has_critical_gaps(self) -> bool:
        return bool(self.missing_actions or self.missing_evidences)


class BundleEvidenceValidator:
    def validate_coverage(
        self, profiles: dict[str, BundleProfile], plan: dict[str, Any]
    ) -> CoverageReport:
        actions: set[str] = set()
        evidences: set[str] = set()
        active: list[str] = []
        for profile in profiles.values():
            nodes = list(selected_nodes(profile, set(plan.get("flags", []))))
            if nodes:
                active.append(profile.id)
            for node, _path in nodes:
                if node.kind != "dir" and node.counts_for_coverage:
                    actions.update(node.trace_back_to.actions)
                    evidences.update(node.trace_back_to.evidences)
        required = {
            item["id"]
            for item in plan.get("actions_meta", [])
            if item.get("requires_evidence", True)
        }
        topics = set(plan.get("articles_overlay", {}))
        return CoverageReport(required, actions, topics, evidences, active)
