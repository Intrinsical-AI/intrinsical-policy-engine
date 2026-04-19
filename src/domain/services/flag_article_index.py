# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Build flag→article index (which articles use which flags).

Maps flags to articles they influence based on action 'when' expressions.
Used for traceability and impact analysis.
"""

from typing import Any

from src.domain.exceptions import AssessmentError, RuleParseError
from src.domain.services.rule_engine import parse_when
from src.domain.types import ArticleId, Flag


def _collect_positive(ast: Any, registry: set[Flag]) -> set[Flag]:
    """Collect positive flags from AST node.

    Args:
        ast: AST node from rule engine
        registry: Set of available flags to match against

    Returns:
        Set of positive flags referenced in the AST
    """
    if not isinstance(ast, tuple):
        return set()
    op = ast[0]
    if op == "has":
        return {ast[1]}
    if op == "any_prefix":
        pref = ast[1]
        return {f for f in registry if f == pref or f.startswith(pref + ".")}
    if op == "not":
        return set()  # do not add negative flags
    if op in ("any", "all"):
        acc = set()
        for n in ast[1]:
            acc |= _collect_positive(n, registry)
        return acc
    return set()


def build_flag_article_index(
    actions: list[dict], articles: dict, flags: dict
) -> dict[Flag, set[ArticleId]]:
    """Map flags to articles they influence based on action 'when' expressions."""
    registry = {f["id"] for f in flags.get("registry", [])}
    out: dict[Flag, set[ArticleId]] = {}
    for a in actions:
        arts = set(a.get("articles", []))
        if not arts:
            continue
        try:
            ast = parse_when(a.get("when"))
        except (RuleParseError, ValueError, KeyError, TypeError, AttributeError) as exc:
            action_id = a.get("id", "<unknown>") if isinstance(a, dict) else "<unknown>"
            raise AssessmentError(
                f"Invalid 'when' expression for action '{action_id}': {exc}"
            ) from exc
        flags_pos = _collect_positive(ast, registry) & registry
        for f in flags_pos:
            out.setdefault(f, set()).update(arts)
    return {k: v for k, v in out.items() if v}
