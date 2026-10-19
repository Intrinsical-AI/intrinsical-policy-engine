# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Validate questions in declaration order against earlier answer emissions.

Missing and null answers emit nothing. Conditions cannot see later answers or
derived flags: derivation closure is evaluated after this questionnaire.
"""

from __future__ import annotations

from intrinsical_policy_engine.domain.conditions import evaluate_condition
from intrinsical_policy_engine.domain.contract_models import QuestionnaireContract
from intrinsical_policy_engine.domain.exceptions import AssessmentError


def eval_questions(
    questions_doc: QuestionnaireContract, answers: dict[str, str | list[str] | None]
) -> set[str]:
    questions = [question for group in questions_doc.groups for question in group.questions]
    known = {question.id for question in questions}
    unknown = set(answers) - known
    if unknown:
        raise AssessmentError(f"unknown question ids: {sorted(unknown)}")
    flags: set[str] = set()
    for question in questions:
        if question.id not in answers or answers[question.id] is None:
            continue
        answer = answers[question.id]
        if not evaluate_condition(question.when, flags):
            raise AssessmentError(f"answer supplied for inactive question: {question.id}")
        if question.type == "text":
            if not isinstance(answer, str) or not answer.strip() or len(answer) > 2000:
                raise AssessmentError(
                    f"question {question.id} requires nonempty text of at most 2000 characters"
                )
            continue
        values = {option.value for option in question.options}
        if question.type == "one_of":
            if not isinstance(answer, str) or answer not in values:
                raise AssessmentError(
                    f"invalid option for question {question.id}; choose one of {sorted(values)}"
                )
            selected = [answer]
        else:
            if not isinstance(answer, list) or any(
                not isinstance(value, str) or value not in values for value in answer
            ):
                raise AssessmentError(f"question {question.id} requires a list of valid options")
            if len(answer) != len(set(answer)):
                raise AssessmentError(f"duplicate options for question {question.id}")
            selected = answer
        for selected_value in selected:
            flags.update(question.map_to_flags.get(selected_value, ()))
    return flags
