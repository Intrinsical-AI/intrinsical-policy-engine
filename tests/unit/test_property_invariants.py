# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Algebraic and boundary properties independent of particular starter packs."""

from __future__ import annotations

from itertools import combinations

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from intrinsical_policy_engine.domain.conditions import Condition, namespace_matches
from intrinsical_policy_engine.domain.contract_models import Derivation, RulesContract
from intrinsical_policy_engine.domain.output_inventory import OutputCollisionError, OutputInventory
from intrinsical_policy_engine.domain.services.derivation_closure import derive_with_trace
from intrinsical_policy_engine.domain.services.evidence_requirements import (
    missing_evidence_requirements,
)
from intrinsical_policy_engine.domain.snapshot_contract import PackageIntegrityError, canonical_path

FINITE_FLAGS = tuple(f"review.f{index}" for index in range(6))
FLAG_SETS = st.sets(st.sampled_from(FINITE_FLAGS))
POSITIVE_CONDITIONS = st.recursive(
    st.one_of(
        st.sampled_from(FINITE_FLAGS).map(lambda flag: {"has": flag}),
        st.just({"any_prefix": "review"}),
    ),
    lambda child: st.one_of(
        st.lists(child, max_size=3).map(lambda children: {"all": children}),
        st.lists(child, max_size=3).map(lambda children: {"any": children}),
    ),
    max_leaves=12,
)
SEGMENT = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789_-", min_size=1, max_size=12)
PATH = st.lists(SEGMENT, min_size=1, max_size=4).map("/".join)
PROPERTY_SETTINGS = settings(max_examples=80, deadline=None, derandomize=True, database=None)


def closure(flags, rules):
    return derive_with_trace(set(flags), rules).final_flags


@PROPERTY_SETTINGS
@given(
    seeds=FLAG_SETS,
    rules=st.lists(st.tuples(FLAG_SETS, FLAG_SETS.filter(bool)), max_size=9),
)
def test_horn_closure_is_the_intersection_of_every_closed_superset(seeds, rules):
    """An exhaustive mathematical oracle detects missing and unjustified flags."""
    contract = RulesContract(
        derivations=tuple(
            Derivation(
                id=f"rule{index}",
                when=Condition.model_validate({"all": [{"has": f} for f in sorted(premises)]}),
                set_flags=tuple(sorted(outputs)),
            )
            for index, (premises, outputs) in enumerate(rules)
        )
    )
    closed_supersets = []
    for size in range(len(FINITE_FLAGS) + 1):
        for subset in combinations(FINITE_FLAGS, size):
            candidate = set(subset)
            if seeds <= candidate and all(
                not premises <= candidate or outputs <= candidate for premises, outputs in rules
            ):
                closed_supersets.append(candidate)
    assert closed_supersets  # The entire finite universe is always closed.
    expected = set.intersection(*closed_supersets)
    assert closure(seeds, contract) == expected


@PROPERTY_SETTINGS
@given(
    initial=FLAG_SETS,
    extra=FLAG_SETS,
    rules=st.lists(st.tuples(POSITIVE_CONDITIONS, FLAG_SETS.filter(bool)), max_size=12),
    ordering=st.randoms(),
)
def test_positive_derivation_closure_is_monotone_idempotent_and_order_independent(
    initial, extra, rules, ordering
):
    derivations = [
        Derivation(
            id=f"rule{index}",
            when=Condition.model_validate(condition),
            set_flags=tuple(sorted(flags)),
        )
        for index, (condition, flags) in enumerate(rules)
    ]
    contract = RulesContract(derivations=tuple(derivations))
    closed = closure(initial, contract)
    assert initial <= closed <= set(FINITE_FLAGS)
    assert closed <= closure(initial | extra, contract)
    assert closure(closed, contract) == closed
    ordering.shuffle(derivations)
    assert closure(initial, RulesContract(derivations=tuple(derivations))) == closed


@PROPERTY_SETTINGS
@given(prefix=st.lists(SEGMENT, min_size=1, max_size=3).map(".".join), child=SEGMENT)
def test_flag_namespace_matching_respects_segment_boundaries(prefix, child):
    assert namespace_matches(prefix, prefix)
    assert namespace_matches(prefix + "." + child, prefix)
    assert not namespace_matches(prefix + child, prefix)
    assert not namespace_matches(prefix + "-backup." + child, prefix)


@PROPERTY_SETTINGS
@given(directory=PATH, child=SEGMENT)
def test_evidence_attribution_uses_paths_and_not_similar_prefixes(directory, child):
    plan = {"actions_meta": [{"id": "a", "evidence": [directory]}]}

    def satisfied(name):
        return missing_evidence_requirements(plan, [name]) == []

    assert satisfied(directory)
    assert satisfied(directory + "/" + child)
    assert not satisfied(directory + "-backup/" + child)
    assert not satisfied(directory + child)


@PROPERTY_SETTINGS
@given(path=PATH, child=SEGMENT)
def test_canonical_paths_round_trip_and_reject_ambiguous_or_escaping_forms(path, child):
    assert canonical_path(path) == path
    for invalid in (
        "/" + path,
        "../" + path,
        path + "/../" + child,
        path + "/./" + child,
        path + "//" + child,
        path + "\\" + child,
        path + ":" + child,
        path + "\n" + child,
    ):
        with pytest.raises(PackageIntegrityError):
            canonical_path(invalid)


@PROPERTY_SETTINGS
@given(parent=PATH, child=SEGMENT)
def test_output_file_ancestor_conflicts_are_rejected_regardless_of_insertion_order(parent, child):
    descendant = parent + "/" + child
    for first, second in ((parent, descendant), (descendant, parent)):
        inventory = OutputInventory()
        inventory.add_file(first, "producer1")
        with pytest.raises(OutputCollisionError):
            inventory.add_file(second, "producer2")


@PROPERTY_SETTINGS
@given(parent=PATH, names=st.sets(SEGMENT, min_size=2, max_size=8))
def test_independent_output_siblings_can_be_registered_in_any_order(parent, names):
    paths = [parent + "/" + name for name in sorted(names)]
    forward, backward = OutputInventory(), OutputInventory()
    for path in paths:
        forward.add_file(path, path)
    for path in reversed(paths):
        backward.add_file(path, path)
    assert forward.files == backward.files
    assert forward.directories == backward.directories
