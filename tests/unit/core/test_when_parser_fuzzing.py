# SPDX-License-Identifier: MPL-2.0
"""Hypothesis robustness contracts for the framework-neutral rule parser."""

from __future__ import annotations

import pytest
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from intrinsical_policy_engine.domain.exceptions import RuleParseError
from intrinsical_policy_engine.domain.services.rule_engine import eval_ast, parse_when


@st.composite
def valid_flag_name(draw: st.DrawFn) -> str:
    parts = draw(
        st.lists(
            st.text(
                min_size=1,
                max_size=10,
                alphabet=st.characters(whitelist_categories=("Lu", "Ll", "Nd")),
            ),
            min_size=1,
            max_size=3,
        )
    )
    return ".".join(parts)


MALFORMED_EXPRESSIONS = st.sampled_from(
    [
        "has('flag'",
        "has('flag'))",
        "((has('flag')",
        "AND",
        "OR",
        "has('a') @@ has('b')",
        "has('flag)",
        "has(flag')",
        "",
        "   ",
        "()",
        "has()",
        "has('a' 'b')",
        "NOT",
    ]
)


@settings(max_examples=50, deadline=None)
@given(expression=MALFORMED_EXPRESSIONS)
def test_malformed_expression_raises_typed_error(expression: str) -> None:
    with pytest.raises(RuleParseError) as exc_info:
        parse_when(expression)

    assert exc_info.value.expression
    assert exc_info.value.reason


@settings(max_examples=100, deadline=None)
@given(expression=st.text(max_size=100))
def test_arbitrary_text_never_leaks_unexpected_exception(expression: str) -> None:
    try:
        ast = parse_when(expression)
        assert isinstance(eval_ast(ast, set()), bool)
    except RuleParseError as exc:
        assert exc.expression is not None
    except Exception as exc:  # noqa: BLE001 - the property forbids every other exception type
        pytest.fail(f"Unexpected exception: {type(exc).__name__}: {exc}")


@settings(max_examples=30, deadline=None)
@given(levels=st.integers(min_value=1, max_value=20))
def test_nested_parentheses_do_not_overflow(levels: int) -> None:
    expression = "(" * levels + "has('X')" + ")" * levels
    try:
        ast = parse_when(expression)
        assert eval_ast(ast, {"X"}) is True
    except RuleParseError:
        pass


@settings(max_examples=50, deadline=None)
@given(flag=valid_flag_name())
def test_has_expression_is_evaluable(flag: str) -> None:
    ast = parse_when(f"has('{flag}')")
    assert eval_ast(ast, {flag}) is True
    assert eval_ast(ast, set()) is False


@settings(max_examples=50, deadline=None)
@given(flag=valid_flag_name())
def test_prefix_wildcard_matches_only_its_namespace(flag: str) -> None:
    assume("." in flag)
    prefix = flag.rsplit(".", 1)[0]
    ast = parse_when(f"any('{prefix}.*')")

    assert eval_ast(ast, {flag}) is True
    assert eval_ast(ast, {prefix}) is True
    assert eval_ast(ast, {f"unrelated_{prefix}.flag"}) is False


@settings(max_examples=50, deadline=None)
@given(first=valid_flag_name(), second=valid_flag_name())
def test_and_truth_table(first: str, second: str) -> None:
    assume(first != second)
    ast = parse_when(f"has('{first}') AND has('{second}')")

    assert eval_ast(ast, {first, second}) is True
    assert eval_ast(ast, {first}) is False
    assert eval_ast(ast, {second}) is False
    assert eval_ast(ast, set()) is False


@settings(max_examples=50, deadline=None)
@given(first=valid_flag_name(), second=valid_flag_name())
def test_or_truth_table(first: str, second: str) -> None:
    assume(first != second)
    ast = parse_when(f"has('{first}') OR has('{second}')")

    assert eval_ast(ast, {first, second}) is True
    assert eval_ast(ast, {first}) is True
    assert eval_ast(ast, {second}) is True
    assert eval_ast(ast, set()) is False


@settings(max_examples=50, deadline=None)
@given(flag=valid_flag_name(), whitespace=st.text(alphabet=" \t", min_size=0, max_size=5))
def test_whitespace_variants_are_semantically_equivalent(flag: str, whitespace: str) -> None:
    canonical = parse_when(f"has('{flag}')")
    spaced = parse_when(f"{whitespace}has({whitespace}'{flag}'{whitespace}){whitespace}")

    assert eval_ast(canonical, {flag}) == eval_ast(spaced, {flag})


@settings(max_examples=30, deadline=None)
@given(length=st.integers(min_value=1, max_value=200))
def test_long_flag_names_remain_evaluable(length: int) -> None:
    flag = "a" * length
    ast = parse_when(f"has('{flag}')")

    assert eval_ast(ast, {flag}) is True
    assert eval_ast(ast, set()) is False


@settings(max_examples=30, deadline=None)
@given(count=st.integers(min_value=1, max_value=30))
def test_has_any_accepts_generated_lists(count: int) -> None:
    flags = [f"flag{index}" for index in range(count)]
    arguments = ", ".join(f"'{flag}'" for flag in flags)
    ast = parse_when(f"has_any([{arguments}])")

    assert all(eval_ast(ast, {flag}) for flag in flags)
    assert eval_ast(ast, set()) is False
