# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Coverage uses the same guarded profile traversal as rendering."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from intrinsical_policy_engine.domain.bundles.models import BundleProfile
from intrinsical_policy_engine.domain.bundles.selection import selected_nodes


@dataclass(frozen=True)
class CoverageReport:
    required_actions: set[str] = field(default_factory=set)
    covered_actions: set[str] = field(default_factory=set)
    required_articles: set[str] = field(default_factory=set)
    covered_articles: set[str] = field(default_factory=set)

    @property
    def missing_actions(self) -> set[str]:
        return self.required_actions - self.covered_actions

    @property
    def missing_articles(self) -> set[str]:
        return self.required_articles - self.covered_articles

    def has_critical_gaps(self) -> bool:
        return bool(self.missing_actions or self.missing_articles)


def profile_coverage(
    profiles: Mapping[str, BundleProfile], plan: Mapping[str, Any]
) -> CoverageReport:
    """Compare selected file/copy nodes' traces with evidence-requiring actions and articles."""
    actions: set[str] = set()
    articles: set[str] = set()
    flags = set(plan.get("flags", []))
    for profile in profiles.values():
        for node, _path in selected_nodes(profile, flags):
            if node.kind != "dir" and node.counts_for_coverage:
                actions.update(node.trace_back_to.actions)
                articles.update(node.trace_back_to.articles)
    required = {
        item["id"] for item in plan.get("actions_meta", []) if item.get("requires_evidence", True)
    }
    return CoverageReport(required, actions, set(plan.get("articles_overlay", {})), articles)
