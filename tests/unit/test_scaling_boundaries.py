# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Attribution stays equivalent while independent inventories grow together."""

from __future__ import annotations

from collections.abc import Collection, Iterator

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from intrinsical_policy_engine.domain.conditions import Condition
from intrinsical_policy_engine.domain.contract_models import (
    ActionDefinition,
    FlagDefinition,
    FlagsContract,
)
from intrinsical_policy_engine.domain.services.evidence_requirements import (
    missing_evidence_requirements,
    ready_evidence_problems,
    required_evidence_by_action,
)
from intrinsical_policy_engine.domain.services.flag_article_index import build_flag_article_index


class CountedFiles(Collection[str]):
    """Observe inventory traversal independently of its matching algorithm."""

    def __init__(self, names: list[str]) -> None:
        self.names = names
        self.available = frozenset(names)
        self.visits = 0
        self.lookups = 0

    def __contains__(self, value: object) -> bool:
        self.lookups += 1
        return value in self.available

    def __iter__(self) -> Iterator[str]:
        for name in self.names:
            self.visits += 1
            yield name

    def __len__(self) -> int:
        return len(self.names)


class CountedQuality(dict[str, str]):
    def __init__(self, values: dict[str, str]) -> None:
        super().__init__(values)
        self.visits = 0

    def items(self):
        for item in super().items():
            self.visits += 1
            yield item


def test_exact_flag_attribution_does_not_walk_unrelated_registry_entries():
    visits = 0

    class CountedRegistry(tuple):
        def __iter__(self):
            nonlocal visits
            for value in super().__iter__():
                visits += 1
                yield value

    registry = CountedRegistry(
        [FlagDefinition(id=f"review.f{index}") for index in range(100)]
        + [FlagDefinition(id=f"unrelated.f{index}") for index in range(200)]
    )
    flags = FlagsContract.model_construct(registry=registry)
    actions = [
        ActionDefinition(
            id=f"action{index}",
            title="Review",
            when=Condition.model_validate({"has": f"review.f{index}"}),
        )
        for index in range(100)
    ]
    articles = {action.id: {f"article{index}"} for index, action in enumerate(actions)}
    expected = {f"review.f{index}": {f"article{index}"} for index in range(len(actions))}
    assert build_flag_article_index(actions, flags, articles) == expected
    assert visits == 0


def test_namespace_fanout_does_not_repeat_full_registry_comparisons_for_each_action():
    comparisons = 0

    class CountedIdentifier(str):
        def startswith(self, prefix, *args):
            nonlocal comparisons
            comparisons += 1
            return super().startswith(prefix, *args)

    names = [f"review.f{index}" for index in range(100)] + ["reviewed.other", "review"]
    flags = FlagsContract.model_construct(
        registry=tuple(FlagDefinition.model_construct(id=CountedIdentifier(name)) for name in names)
    )
    actions = [
        ActionDefinition(
            id=f"action{index}",
            title="Review",
            when=Condition.model_validate({"any_prefix": "review"}),
        )
        for index in range(100)
    ]
    articles = {action.id: {f"article{index}"} for index, action in enumerate(actions)}
    expected_articles = set().union(*articles.values())
    result = build_flag_article_index(actions, flags, articles)
    assert result == {name: expected_articles for name in names if name != "reviewed.other"}
    assert comparisons <= 4 * (len(names) + len(actions))


@pytest.mark.parametrize("prefix", ["review", "review.", ".", "x", "x.y"])
def test_namespace_attribution_matches_the_segment_oracle_including_empty_segments(prefix):
    names = ["review", "review.a", "review..b", "reviewed.a", ".hidden", "..nested", "x.y", "x.y.z"]
    flags = FlagsContract(registry=tuple(FlagDefinition(id=name) for name in names))
    action = ActionDefinition(
        id="review", title="Review", when=Condition.model_validate({"any_prefix": prefix})
    )
    expected = {
        name: {"article"} for name in names if name == prefix or name.startswith(prefix + ".")
    }
    assert build_flag_article_index([action], flags, {"review": {"article"}}) == expected


def test_evidence_union_keeps_order_deduplication_optional_actions_and_inverse_relations():
    plan = {
        "actions_meta": [
            {"id": "a", "evidence": ["direct.json", "shared"], "articles": ["art.b"]},
            {"id": "optional", "requires_evidence": False, "evidence": ["optional.json"]},
            {"id": "empty"},
        ],
        "actions_evidence_map": {"a": ["mapped.json", "direct.json"]},
        "articles_overlay": {"art.a": ["a", "a"], "unused": ["unknown-action"]},
        "articles_evidence_map": {
            "art.a": ["inherited-a.json", "shared"],
            "art.b": ["direct.json", "inherited-b.json"],
            "unused": ["unused.json"],
        },
    }
    assert required_evidence_by_action(plan) == {
        "a": ("direct.json", "shared", "mapped.json", "inherited-a.json", "inherited-b.json"),
        "empty": (),
    }
    assert required_evidence_by_action({}) == {}
    assert ready_evidence_problems({}, {"unknown.json": "ready"}, ["unknown.json"]) == []


@pytest.mark.parametrize(
    "plan,match",
    [
        ({"actions_meta": [{"id": "a"}, {"id": "a"}]}, "Duplicate action metadata"),
        ({"actions_evidence_map": {"unknown": ["review.json"]}}, "without metadata"),
        ({"actions_meta": [{"id": "a", "requires_evidence": "yes"}]}, "boolean"),
        ({"actions_meta": [{"id": "a", "evidence": ["../outside.json"]}]}, "Unsafe"),
        ({"articles_overlay": {"article": [12]}}, "list of strings"),
    ],
)
def test_invalid_and_unattributed_evidence_metadata_is_still_rejected(plan, match):
    with pytest.raises(ValueError, match=match):
        required_evidence_by_action(plan)


def test_inverse_overlay_is_not_rescanned_for_each_selected_action():
    membership_tests = 0
    visits = 0

    class CountedActions(list):
        def __contains__(self, value):
            nonlocal membership_tests
            membership_tests += 1
            return super().__contains__(value)

        def __iter__(self):
            nonlocal visits
            for value in super().__iter__():
                visits += 1
                yield value

    size = 100
    plan = {
        "actions_meta": [{"id": f"action{index}"} for index in range(size)],
        "articles_overlay": {
            f"article{index}": CountedActions([f"action{index}"]) for index in range(size)
        },
        "articles_evidence_map": {
            f"article{index}": [f"review{index}.json"] for index in range(size)
        },
    }
    assert required_evidence_by_action(plan) == {
        f"action{index}": (f"review{index}.json",) for index in range(size)
    }
    assert membership_tests <= size * 3
    assert visits <= size * 3


@pytest.mark.parametrize("directory_requirements", [False, True])
def test_evidence_completeness_matches_a_naive_oracle_without_repeated_inventory_scans(
    directory_requirements,
):
    size = 100
    names = [f"review{index}/record.json" for index in range(size)]
    paths = [f"review{index}" if directory_requirements else names[index] for index in range(size)]
    quality = {name: "ready" if index % 3 == 0 else "draft" for index, name in enumerate(names)}
    quality["unknown/absent.json"] = "ready"
    plan = {
        "actions_meta": [{"id": f"action{index}"} for index in range(size)],
        "actions_evidence_map": {f"action{index}": [path] for index, path in enumerate(paths)},
    }
    expected = [
        f"CI-002: action action{index} has no ready evidence for {path}"
        for index, path in enumerate(paths)
        if not any(
            name in names and status == "ready" and (name == path or name.startswith(path + "/"))
            for name, status in quality.items()
        )
    ]
    files = CountedFiles(names)
    assert missing_evidence_requirements(plan, files) == []
    assert files.visits <= size * 2
    files = CountedFiles(names)
    observed = CountedQuality(quality)
    assert ready_evidence_problems(plan, observed, files) == expected
    assert observed.visits <= len(quality) * 2
    assert files.visits <= size * 2
    assert files.lookups <= size * 2


def test_ready_directory_lookup_preserves_exact_presence_quality_and_segment_boundaries():
    paths = [
        "review",
        "review/record.json",
        "reviewed",
        "draft.json",
        "ghost.json",
        "unscored.json",
    ]
    plan = {
        "actions_meta": [{"id": "a"}],
        "actions_evidence_map": {"a": paths},
    }
    files = [
        "review/nested/ready.json",
        "review/record.json",
        "reviewed-other/ready.json",
        "draft.json",
        "unscored.json",
    ]
    quality = {
        "review/nested/ready.json": "ready",
        "review/record.json": "draft",
        "reviewed-other/ready.json": "ready",
        "draft.json": "draft",
        "ghost.json": "ready",
    }
    assert missing_evidence_requirements(plan, files) == [
        "Missing evidence: reviewed (action a)",
        "Missing evidence: ghost.json (action a)",
    ]
    assert ready_evidence_problems(plan, quality, files) == [
        f"CI-002: action a has no ready evidence for {path}" for path in paths[1:]
    ]


def test_empty_requirements_keep_their_distinct_diagnostics():
    plan = {"actions_meta": [{"id": "a"}]}
    assert missing_evidence_requirements(plan, []) == ["Missing evidence requirements for action a"]
    assert ready_evidence_problems(plan, {}, []) == [
        "CI-002: action a has no declared evidence requirements"
    ]


EVIDENCE_NAMES = (
    "review",
    "review/one.json",
    "review/nested/two.json",
    "review/one.json/child.json",
    "reviewed/one.json",
    "review-other/one.json",
    "other.json",
)


@settings(max_examples=120, deadline=None, derandomize=True, database=None)
@given(
    requirements=st.sets(st.sampled_from(EVIDENCE_NAMES), min_size=1),
    files=st.sets(st.sampled_from(EVIDENCE_NAMES)),
    quality=st.dictionaries(
        st.sampled_from(EVIDENCE_NAMES),
        st.sampled_from(["ready", "draft", "placeholder", "unknown"]),
    ),
)
def test_indexed_evidence_matching_agrees_with_the_naive_oracle(requirements, files, quality):
    paths = sorted(requirements)
    plan = {"actions_meta": [{"id": "a"}], "actions_evidence_map": {"a": paths}}
    missing = [
        f"Missing evidence: {path} (action a)"
        for path in paths
        if not any(name == path or name.startswith(path + "/") for name in files)
    ]
    unready = [
        f"CI-002: action a has no ready evidence for {path}"
        for path in paths
        if not any(
            name in files and status == "ready" and (name == path or name.startswith(path + "/"))
            for name, status in quality.items()
        )
    ]
    assert missing_evidence_requirements(plan, files) == missing
    assert ready_evidence_problems(plan, quality, files) == unready
