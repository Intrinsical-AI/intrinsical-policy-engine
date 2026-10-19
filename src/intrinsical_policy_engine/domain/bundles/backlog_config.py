# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pure keyword filtering using the compiled backlog configuration."""

from __future__ import annotations

from intrinsical_policy_engine.domain.bundles.models import BacklogConfig


def build_backlog_config(data: object) -> BacklogConfig:
    return BacklogConfig.model_validate(data)


def get_keywords_for_split(config: BacklogConfig, split_id: str) -> set[str]:
    for rule in config.splits:
        if rule.id == split_id:
            return {keyword.upper() for keyword in rule.keywords}
    raise ValueError(f"unknown backlog split id: {split_id}")


def filter_actions_by_keywords(
    actions: list[dict], keywords: set[str], *, include_unmatched: bool = False
) -> list[dict]:
    if not keywords:
        return list(actions) if include_unmatched else []
    return [
        action
        for action in actions
        if any(keyword in (action["id"] + " " + action["title"]).upper() for keyword in keywords)
    ]


def split_actions(actions: list[dict], config: BacklogConfig) -> dict[str, list[dict]]:
    grouped: dict[str, list[dict]] = {split.id: [] for split in config.splits}
    for action in actions:
        chosen = next(
            (
                split.id
                for split in config.splits
                if filter_actions_by_keywords([action], get_keywords_for_split(config, split.id))
            ),
            config.fallback_split_id,
        )
        grouped[chosen].append(action)
    return grouped
