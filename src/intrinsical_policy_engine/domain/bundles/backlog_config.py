# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pure keyword filtering using the compiled backlog configuration."""

from __future__ import annotations

from intrinsical_policy_engine.domain.bundles.models import BacklogConfig


def split_actions(actions: list[dict], config: BacklogConfig) -> dict[str, list[dict]]:
    """Assign each action to the first split with a keyword in its ID or title.

    Matching is case-insensitive; a split without keywords never matches, and
    unmatched actions go to ``fallback_split_id``.
    """
    keywords = {
        split.id: {keyword.upper() for keyword in split.keywords} for split in config.splits
    }
    grouped: dict[str, list[dict]] = {split.id: [] for split in config.splits}
    for action in actions:
        text = (action["id"] + " " + action["title"]).upper()
        chosen = next(
            (
                split_id
                for split_id, words in keywords.items()
                if any(word in text for word in words)
            ),
            config.fallback_split_id,
        )
        grouped[chosen].append(action)
    return grouped
