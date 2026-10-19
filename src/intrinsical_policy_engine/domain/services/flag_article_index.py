# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Build flag to article attribution from the compiled AST."""

from collections.abc import Sequence

from intrinsical_policy_engine.domain.conditions import condition_references
from intrinsical_policy_engine.domain.contract_models import ActionDefinition, FlagsContract


def build_flag_article_index(
    actions: Sequence[ActionDefinition], flags: FlagsContract, action_articles: dict[str, set[str]]
) -> dict[str, set[str]]:
    references = [(action, *condition_references(action.when)) for action in actions]
    namespaces: dict[str, set[str]] = {
        prefix: set() for _, _, prefixes in references for prefix in prefixes
    }
    # Exact references need no registry expansion. For namespaces, visit each
    # registry entry once and index only the requested segment prefixes.
    if namespaces:
        prefix_lengths = {len(prefix) for prefix in namespaces}
        for definition in flags.registry:
            flag = definition.id
            if flag in namespaces:
                namespaces[flag].add(flag)
            for index, character in enumerate(flag):
                if character == "." and index in prefix_lengths:
                    prefix = flag[:index]
                    if prefix in namespaces:
                        namespaces[prefix].add(flag)
    result: dict[str, set[str]] = {}
    for action, exact, prefixes in references:
        referenced = exact.union(*(namespaces[prefix] for prefix in prefixes))
        for flag in referenced:
            result.setdefault(flag, set()).update(action_articles.get(action.id, set()))
    return result
