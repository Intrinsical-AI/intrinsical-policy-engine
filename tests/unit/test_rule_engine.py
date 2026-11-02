# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Structured rule evaluation and positive least-fixed-point behavior."""

import pytest
from pydantic import ValidationError

from intrinsical_policy_engine.domain.conditions import Condition, evaluate_condition
from intrinsical_policy_engine.domain.contract_models import RulesContract
from intrinsical_policy_engine.domain.services.derivation_closure import derive_with_trace


def closure(flags, rules):
    return derive_with_trace(set(flags), rules).final_flags


@pytest.mark.parametrize(
    "source",
    ["has('f')", {"has": 42}, {"has": "f", "not": {"has": "g"}}, {"unsupported": "f"}, None],
)
def test_invalid_condition_is_rejected_at_compilation(source):
    with pytest.raises(ValidationError):
        Condition.model_validate(source)


def test_namespace_prefix_does_not_match_similar_identifier():
    condition = Condition.model_validate({"any_prefix": "p.a"})
    assert evaluate_condition(condition, {"p.a"})
    assert evaluate_condition(condition, {"p.a.child"})
    assert not evaluate_condition(condition, {"p.ab"})


def test_positive_cycles_need_a_seed_and_do_not_depend_on_order():
    rules = [
        {"id": "forward", "when": {"has": "a"}, "set_flags": ["b"]},
        {"id": "back", "when": {"has": "b"}, "set_flags": ["a"]},
    ]
    for ordered in (rules, list(reversed(rules))):
        contract = RulesContract.model_validate({"derivations": ordered})
        assert closure(set(), contract) == set()
        assert closure({"a"}, contract) == {"a", "b"}


def test_long_chain_converges_without_an_arbitrary_iteration_cap():
    rules = [
        {"id": f"d{i}", "when": {"has": f"f{i}"}, "set_flags": [f"f{i + 1}"]} for i in range(130)
    ]
    contract = RulesContract.model_validate({"derivations": list(reversed(rules))})
    assert closure({"f0"}, contract) == {f"f{i}" for i in range(131)}


def test_not_is_rejected_only_in_derivations():
    condition = {"not": {"has": "a"}}
    with pytest.raises(ValidationError, match="positive"):
        RulesContract.model_validate(
            {"derivations": [{"id": "negative", "when": condition, "set_flags": ["b"]}]}
        )
    assert evaluate_condition(Condition.model_validate(condition), set())


def test_empty_boolean_lists_have_explicit_semantics():
    assert evaluate_condition(Condition.model_validate({"all": []}), set())
    assert not evaluate_condition(Condition.model_validate({"any": []}), set())
