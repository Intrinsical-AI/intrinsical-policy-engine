# SPDX-License-Identifier: MPL-2.0
"""Privacy boundary tests for assessment traces."""

from __future__ import annotations

from intrinsical_policy_engine.domain.services.tracer import build_trace


def _build(*, include_full_trace: bool = False, include_raw_answers: bool = False):
    answers = {"question": "sensitive answer", "contact": "person@example.test"}
    return build_trace(
        answers,
        {"flag.initial"},
        {"flag.initial", "flag.derived"},
        {"rule": True},
        ["CONTROL-1"],
        {"CONTROL-1": "2027-01-01"},
        {"TOPIC-1": ["CONTROL-1"]},
        include_full_trace=include_full_trace,
        include_raw_answers=include_raw_answers,
    )


def test_trace_excludes_raw_answers_by_default() -> None:
    answers_raw = _build()["answers_raw"]

    assert "answers" not in answers_raw
    assert "answers_hash" in answers_raw


def test_full_trace_contains_only_sanitized_values() -> None:
    answers_raw = _build(include_full_trace=True)["answers_raw"]

    assert "answers" not in answers_raw
    assert "answers_hash" in answers_raw
    assert "answers_keys" in answers_raw
    sanitized = answers_raw["answers_sanitized"]
    assert sanitized["question"] != "sensitive answer"
    assert sanitized["question"].startswith("[hash:")


def test_raw_answers_require_explicit_opt_in() -> None:
    answers_raw = _build(include_raw_answers=True)["answers_raw"]

    assert answers_raw["answers"] == {
        "question": "sensitive answer",
        "contact": "person@example.test",
    }
