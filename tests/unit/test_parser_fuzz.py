# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Bounded parser probes exercise malformed input and preserve caller files."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from hypothesis import HealthCheck, example, given, settings
from hypothesis import strategies as st

from intrinsical_policy_engine.adapters.quality.engine import QualityEngine
from intrinsical_policy_engine.common.document_io import parse_document
from intrinsical_policy_engine.domain.conditions import Condition, evaluate_condition

MAX_INPUT_BYTES = 2048
FUZZ_SETTINGS = settings(
    max_examples=80,
    # Instrumentation and interpreter speed must not turn controlled rejection
    # into a timing failure. Cases and bytes remain bounded; benchmark budgets
    # are measured separately without coverage instrumentation.
    deadline=None,
    derandomize=True,
    database=None,
    # Every example deliberately rewrites the same caller file and checks its
    # bytes after the operation; no fixture state is relied on across examples.
    suppress_health_check=(HealthCheck.function_scoped_fixture,),
)
SCALAR_TEXT = st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789 ._é", max_size=16)
JSON_VALUES = st.recursive(
    st.one_of(st.none(), st.booleans(), st.integers(-1_000_000, 1_000_000), SCALAR_TEXT),
    lambda child: st.one_of(
        st.lists(child, max_size=3), st.dictionaries(SCALAR_TEXT, child, max_size=3)
    ),
    max_leaves=10,
)
UTF8_TEXT = st.text(st.characters(exclude_categories=["Cs"]), max_size=512)
CONDITION_VALUES = st.recursive(
    st.sampled_from(("review.a", "review.b", "review.child")).map(lambda flag: {"has": flag}),
    lambda child: st.one_of(
        child.map(lambda value: {"not": value}),
        st.lists(child, max_size=3).map(lambda values: {"all": values}),
        st.lists(child, max_size=3).map(lambda values: {"any": values}),
    ),
    max_leaves=12,
)


def encode_document(value: Any, suffix: str) -> bytes:
    text = (
        json.dumps(value, ensure_ascii=False)
        if suffix == ".json"
        else yaml.safe_dump(value, allow_unicode=True)
    )
    data = text.encode("utf-8")
    assert len(data) <= MAX_INPUT_BYTES
    return data


@FUZZ_SETTINGS
@given(content=UTF8_TEXT, suffix=st.sampled_from((".json", ".yaml", ".yml")))
@example(content="[" * 1000 + "]" * 1000, suffix=".json")
@example(content="[" * 600 + "]" * 600, suffix=".yaml")
@example(content="&loop [*loop]", suffix=".yaml")
@example(content="root: &loop [*loop]", suffix=".yaml")
@example(content="first: &item [one, two]\nsecond: *item\n", suffix=".yaml")
@example(content="key: first\nkey: second\n", suffix=".yaml")
def test_bounded_document_text_has_only_controlled_parse_failures(
    content: str, suffix: str
) -> None:
    assert len(content.encode("utf-8")) <= MAX_INPUT_BYTES
    try:
        parse_document(content, suffix)
    except (ValueError, yaml.YAMLError):
        # Invalid authoring input may fail; RecursionError, TypeError and other
        # programming errors must escape and fail this property.
        return


@FUZZ_SETTINGS
@given(value=JSON_VALUES, suffix=st.sampled_from((".json", ".yaml")))
def test_valid_nested_documents_preserve_authored_values(value: Any, suffix: str) -> None:
    data = encode_document(value, suffix)
    parsed = parse_document(data.decode("utf-8"), suffix)
    # JSON comparison distinguishes bools from numbers; Python == would not.
    assert json.dumps(parsed, sort_keys=True) == json.dumps(value, sort_keys=True)


@pytest.mark.parametrize("suffix", (".json", ".yaml"))
def test_compact_excessive_nesting_is_rejected_before_recursive_consumers(suffix: str) -> None:
    content = "[" * 1000 + "]" * 1000
    assert len(content.encode()) <= MAX_INPUT_BYTES
    with pytest.raises((ValueError, yaml.YAMLError)):
        parse_document(content, suffix)


def test_recursive_yaml_alias_is_rejected_at_document_boundary() -> None:
    with pytest.raises((ValueError, yaml.YAMLError)):
        parse_document("&loop [*loop]", ".yaml")


def test_small_nonrecursive_yaml_alias_remains_valid_and_preserves_source(tmp_path: Path) -> None:
    data = b"first: &record [complete, reviewed]\nsecond: *record\nthird: signed\n"
    parsed = parse_document(data.decode(), ".yaml")
    assert parsed == {
        "first": ["complete", "reviewed"],
        "second": ["complete", "reviewed"],
        "third": "signed",
    }
    path = tmp_path / "shared-records.yaml"
    path.write_bytes(data)
    assert QualityEngine().classify_file(path) == "ready"
    assert path.read_bytes() == data


def test_small_yaml_alias_dag_cannot_request_exponential_expansion() -> None:
    lines = ["n0: &n0 [complete]"]
    for depth in range(1, 20):
        lines.append(f"n{depth}: &n{depth} [*n{depth - 1}, *n{depth - 1}]")
    content = "\n".join(lines)
    assert len(content.encode()) <= MAX_INPUT_BYTES
    with pytest.raises((ValueError, yaml.YAMLError)):
        parse_document(content, ".yaml")


@FUZZ_SETTINGS
@given(
    data=st.binary(max_size=MAX_INPUT_BYTES),
    suffix=st.sampled_from((".md", ".json", ".yaml", ".csv")),
)
@example(data=b"\xff\xfe\x00", suffix=".yaml")
@example(data=b"&loop [*loop]", suffix=".yaml")
@example(data=("[" * 600 + "]" * 600).encode(), suffix=".json")
@example(data=("[" * 600 + "]" * 600).encode(), suffix=".yaml")
@example(data=b"root: [one, {nested: [two, three]}]\n", suffix=".yaml")
def test_quality_handles_bounded_bytes_without_changing_caller_file(
    tmp_path: Path, data: bytes, suffix: str
) -> None:
    path = tmp_path / ("evidence" + suffix)
    path.write_bytes(data)
    try:
        quality = QualityEngine().classify_file(path)
    except (ValueError, yaml.YAMLError):
        assert suffix in {".json", ".yaml", ".csv"}
    else:
        assert quality in {"ready", "draft", "placeholder"}
        try:
            data.decode("utf-8")
        except UnicodeDecodeError:
            assert quality == "placeholder"
    finally:
        assert path.read_bytes() == data


@FUZZ_SETTINGS
@given(
    marker=st.sampled_from(("TODO", "[FILL: owner]", "[REQUIRED: owner]", "lorem")),
    wrappers=st.lists(st.booleans(), max_size=12),
    suffix=st.sampled_from((".json", ".yaml")),
)
def test_nested_placeholder_cannot_be_hidden_by_ready_metadata(
    tmp_path: Path, marker: str, wrappers: list[bool], suffix: str
) -> None:
    nested: Any = {"review_note": marker}
    for mapping in wrappers:
        nested = {"details": nested} if mapping else [nested]
    data = encode_document({"status": "ready", "reviewed": True, "result": nested}, suffix)
    path = tmp_path / ("nested-evidence" + suffix)
    path.write_bytes(data)
    assert QualityEngine().classify_file(path) == "placeholder"
    assert path.read_bytes() == data


@pytest.mark.parametrize(
    "nested",
    ("!!omap\n  - note: TODO", "!!pairs\n  - note: TODO", "!!set\n  TODO: null"),
)
def test_yaml_tagged_collections_cannot_hide_placeholder_values(
    tmp_path: Path, nested: str
) -> None:
    data = ("one: complete\ntwo: reviewed\nthree: " + nested + "\n").encode()
    path = tmp_path / "tagged-values.yaml"
    path.write_bytes(data)
    assert QualityEngine().classify_file(path) != "ready"
    assert path.read_bytes() == data


@FUZZ_SETTINGS
@given(field=st.text(alphabet='abcdefghijklmnopqrstuvwxyz ,"\r\n', max_size=256))
def test_csv_characters_after_a_closed_quote_are_rejected_without_rewriting(
    tmp_path: Path, field: str
) -> None:
    escaped = field.replace('"', '""')
    data = ('review,result\n"' + escaped + '"junk,complete\n').encode("utf-8")
    assert len(data) <= MAX_INPUT_BYTES
    path = tmp_path / "malformed.csv"
    path.write_bytes(data)
    with pytest.raises(ValueError, match="Invalid CSV evidence"):
        QualityEngine().classify_file(path)
    assert path.read_bytes() == data


@FUZZ_SETTINGS
@given(shape=st.one_of(JSON_VALUES, CONDITION_VALUES))
@example(shape={"has": []})
@example(shape={"all": [{"unknown": "review.a"}]})
@example(shape={"not": {"any": [{"has": "review.a"}, {"has": "review.b"}]}})
def test_condition_shapes_fail_cleanly_or_evaluate_without_mutating_input(shape: Any) -> None:
    before = json.dumps(shape, sort_keys=True)
    assert len(before.encode("utf-8")) <= MAX_INPUT_BYTES
    try:
        condition = Condition.model_validate(shape)
    except ValueError:
        return
    finally:
        assert json.dumps(shape, sort_keys=True) == before
    flags = frozenset({"review.a", "review.child"})
    result = evaluate_condition(condition, flags)
    assert type(result) is bool
    restored = Condition.model_validate(condition.model_dump(mode="json", by_alias=True))
    assert evaluate_condition(restored, flags) is result
