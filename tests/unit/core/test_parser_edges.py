# SPDX-License-Identifier: MPL-2.0
"""Edge contracts for the string rule parser and evaluator."""

from __future__ import annotations

from typing import Any, cast

import pytest

from intrinsical_policy_engine.domain.exceptions import RuleEvaluationError, RuleParseError
from intrinsical_policy_engine.domain.services.rule_engine import AllOf, AnyOf, eval_ast, parse_when
from intrinsical_policy_engine.domain.types import ASTNode


def test_nested_not_and_or() -> None:
    ast = parse_when("NOT ( has('a') and ( has('b') or has('c') ) )")
    assert eval_ast(ast, {"a", "b"}) is False
    assert eval_ast(ast, {"a"}) is True


@pytest.mark.parametrize(
    "expression",
    [
        "ANY('group.*')",
        "Has('enabled') and NOT(Has('disabled'))",
        "has_any(['alpha','beta','gamma'])",
    ],
)
def test_function_names_are_case_insensitive(expression: str) -> None:
    assert parse_when(expression) is not None


@pytest.mark.parametrize("invalid", ["has(", "foo AND", {"unknown": "value"}])
def test_invalid_expressions_raise_actionable_error(invalid: Any) -> None:
    with pytest.raises(RuleParseError) as exc_info:
        parse_when(invalid)

    assert exc_info.value.expression
    assert exc_info.value.reason


def test_any_exact_value_and_prefix_have_distinct_semantics() -> None:
    assert eval_ast(parse_when("any('group')"), {"group"}) is True
    assert eval_ast(parse_when("any('group')"), {"group.child"}) is False
    assert eval_ast(parse_when("any('group.*')"), {"group"}) is True
    assert eval_ast(parse_when("any('group.*')"), {"group.child"}) is True


def test_empty_boolean_nodes_follow_identity_rules() -> None:
    assert eval_ast(AllOf(), set()) is True
    assert eval_ast(AnyOf(), set()) is False


def test_unknown_ast_operator_raises_rule_evaluation_error() -> None:
    invalid_ast = cast(ASTNode, ("UNKNOWN_OP", "flag"))
    with pytest.raises(RuleEvaluationError, match="Unknown AST operator"):
        eval_ast(invalid_ast, {"flag"})
