# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Regression tests for the combined recursion budget of ``when`` expressions."""

from __future__ import annotations

import pytest

from intrinsical_policy_engine.domain.exceptions import RuleParseError
from intrinsical_policy_engine.domain.services.rule_engine import (
    MAX_PARSE_DEPTH,
    _parse_when_string_cached,
    parse_when,
    validate_when,
)


def _parenthesized_expression(depth: int) -> str:
    expression = "has('leaf_flag')"
    for _ in range(depth):
        expression = f"({expression})"
    return expression


def test_string_nesting_at_boundary_is_accepted() -> None:
    assert parse_when(_parenthesized_expression(MAX_PARSE_DEPTH - 1)) is not None


def test_string_nesting_past_boundary_is_rejected() -> None:
    with pytest.raises(RuleParseError, match="too deeply nested"):
        parse_when(_parenthesized_expression(MAX_PARSE_DEPTH))


def test_mixed_dict_and_string_depths_share_one_budget() -> None:
    expression: dict | str = "(has('leaf_flag'))"
    for _ in range(MAX_PARSE_DEPTH - 1):
        expression = {"not": expression}

    with pytest.raises(RuleParseError, match="too deeply nested"):
        parse_when(expression)


def test_cached_string_reports_relative_depth() -> None:
    parsed = _parse_when_string_cached("((has('leaf_flag')))")

    assert parsed.relative_depth == 3


def test_cache_key_is_not_fragmented_by_dict_depth() -> None:
    _parse_when_string_cached.cache_clear()

    parse_when("(has('leaf_flag'))", _depth=1)
    first_info = _parse_when_string_cached.cache_info()
    parse_when("(has('leaf_flag'))", _depth=2)
    second_info = _parse_when_string_cached.cache_info()

    assert second_info.hits == first_info.hits + 1
    assert second_info.currsize == first_info.currsize


def test_validate_when_enforces_the_same_depth_limit() -> None:
    expression: dict = {"has": "leaf_flag"}
    for _ in range(MAX_PARSE_DEPTH + 1):
        expression = {"not": expression}

    with pytest.raises(RuleParseError, match="too deeply nested"):
        validate_when(expression)
