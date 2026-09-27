# SPDX-License-Identifier: MPL-2.0
"""Precedence, nesting, lexical-error, and determinism tests for the DSL parser."""

from __future__ import annotations

import pytest

from intrinsical_policy_engine.domain.exceptions import RuleParseError
from intrinsical_policy_engine.domain.services.rule_engine import eval_ast, parse_when


class TestPrecedenceAndNesting:
    def test_not_and_or_precedence_with_explicit_parentheses(self) -> None:
        ast = parse_when("NOT (has('A') AND (has('B') OR NOT has('C')))")

        assert eval_ast(ast, {"A", "B", "C"}) is False
        assert eval_ast(ast, {"A", "C"}) is True
        assert eval_ast(ast, {"A"}) is False
        assert eval_ast(ast, {"B"}) is True
        assert eval_ast(ast, set()) is True

    def test_triple_nested_not_equals_single_not(self) -> None:
        triple = parse_when("NOT NOT NOT has('X')")
        single = parse_when("NOT has('X')")

        assert eval_ast(triple, {"X"}) == eval_ast(single, {"X"})
        assert eval_ast(triple, set()) == eval_ast(single, set())

    def test_and_has_higher_precedence_than_or(self) -> None:
        ast = parse_when("has('A') OR has('B') AND has('C')")

        assert eval_ast(ast, {"B", "C"}) is True
        assert eval_ast(ast, {"A"}) is True
        assert eval_ast(ast, {"B"}) is False

    def test_mixed_boolean_operators_allow_explicit_grouping(self) -> None:
        ast = parse_when("has('A') OR (has('B') AND has('C'))")

        assert eval_ast(ast, {"A"}) is True
        assert eval_ast(ast, {"B", "C"}) is True
        assert eval_ast(ast, {"B"}) is False

    def test_five_levels_of_grouping_are_supported(self) -> None:
        ast = parse_when("(((((has('X'))))))")

        assert eval_ast(ast, {"X"}) is True
        assert eval_ast(ast, set()) is False

    def test_prefix_boolean_and_negation_compose(self) -> None:
        ast = parse_when("any('actor.*') AND NOT has('state.denied')")

        assert eval_ast(ast, {"actor.source"}) is True
        assert eval_ast(ast, {"actor.source", "state.denied"}) is False
        assert eval_ast(ast, {"unrelated.flag"}) is False


class TestLexicalErrors:
    def test_missing_closing_parenthesis_is_actionable(self) -> None:
        with pytest.raises(RuleParseError) as exc_info:
            parse_when("has('A') AND (has('B')")
        assert "(" in str(exc_info.value) or "paren" in str(exc_info.value).lower()

    def test_extra_closing_parenthesis_is_controlled(self) -> None:
        with pytest.raises(RuleParseError) as exc_info:
            parse_when("has('A')) AND has('B')")
        assert exc_info.value.expression

    def test_unknown_function_name_is_reported(self) -> None:
        with pytest.raises(RuleParseError) as exc_info:
            parse_when("unknown_func('X')")
        assert "unknown_func" in exc_info.value.expression.lower()

    def test_unclosed_quote_is_controlled(self) -> None:
        with pytest.raises(RuleParseError) as exc_info:
            parse_when("has('X)")
        assert exc_info.value.expression

    @pytest.mark.parametrize("expression", ["AND", "OR", "NOT"])
    def test_operator_without_operand_fails(self, expression: str) -> None:
        with pytest.raises(RuleParseError):
            parse_when(expression)

    @pytest.mark.parametrize(
        "expression",
        [
            "has('flag') @@ has('other')",
            "has('flag') 123",
            "has('X') <> has('Y')",
        ],
    )
    def test_invalid_tokens_fail(self, expression: str) -> None:
        with pytest.raises(RuleParseError) as exc_info:
            parse_when(expression)
        assert exc_info.value.expression


class TestParserEdgeCases:
    def test_excessive_whitespace_is_tolerated(self) -> None:
        ast = parse_when("  has( 'A' )   AND   has(  'B'  )  ")
        assert eval_ast(ast, {"A", "B"}) is True

    def test_utf8_flag_names_are_preserved(self) -> None:
        ast = parse_when("has('flăg.ñame')")
        assert eval_ast(ast, {"flăg.ñame"}) is True
        assert eval_ast(ast, {"other"}) is False

    def test_operator_case_is_ignored(self) -> None:
        lower = parse_when("has('A') and has('B')")
        upper = parse_when("has('A') AND has('B')")
        mixed = parse_when("has('A') AnD has('B')")

        assert eval_ast(lower, {"A", "B"}) == eval_ast(upper, {"A", "B"})
        assert eval_ast(upper, {"A", "B"}) == eval_ast(mixed, {"A", "B"})

    @pytest.mark.parametrize("expression", ["", "   ", "()", "((()))"])
    def test_empty_grouping_fails(self, expression: str) -> None:
        with pytest.raises(RuleParseError):
            parse_when(expression)


class TestDeterministicParsing:
    def test_cached_parse_returns_same_ast_object(self) -> None:
        expression = "has('X') AND (has('Y') OR NOT has('Z'))"

        first = parse_when(expression)
        second = parse_when(expression)

        assert first == second
        assert first is second

    def test_whitespace_variants_normalize_to_same_ast(self) -> None:
        compact = parse_when("has('A')AND has('B')")
        expanded = parse_when("has('A')  AND   has('B')")
        canonical = parse_when("has('A') AND has('B')")

        assert compact == expanded == canonical
