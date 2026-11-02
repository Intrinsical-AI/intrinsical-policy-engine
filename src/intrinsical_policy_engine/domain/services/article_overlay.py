# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Article to selected action mappings over typed contracts."""

from collections.abc import Sequence

from intrinsical_policy_engine.domain.contract_models import ActionDefinition, ArticlesContract


def overlay(
    actions: Sequence[ActionDefinition], articles: ArticlesContract, selected_ids: set[str]
) -> dict[str, set[str]]:
    result = {
        article.id: set(article.related_actions) & selected_ids for article in articles.taxonomy
    }
    for action in actions:
        if action.id in selected_ids:
            for article_id in action.articles:
                result.setdefault(article_id, set()).add(action.id)
    return {key: value for key, value in result.items() if value}


def articles_by_action(article_actions: dict[str, set[str]]) -> dict[str, set[str]]:
    result: dict[str, set[str]] = {}
    for article, action_ids in article_actions.items():
        for action_id in action_ids:
            result.setdefault(action_id, set()).add(article)
    return result
