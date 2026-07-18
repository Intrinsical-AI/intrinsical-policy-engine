# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Questionnaire engine: evaluates user answers and emits flags."""

import logging
from typing import Any

from intrinsical_policy_engine.domain.constants import MAX_TEXT_LENGTH
from intrinsical_policy_engine.domain.exceptions import RuleParseError
from intrinsical_policy_engine.domain.i18n_defaults import LABEL_NO, LABEL_UNKNOWN, LABEL_YES
from intrinsical_policy_engine.domain.services.rule_engine import eval_ast, parse_when
from intrinsical_policy_engine.domain.types import Flag, QuestionnaireDoc, UserAnswers

# Allowed question types across UI/CLI
# Note: "choice" is an alias for "one_of" used in some questionnaire YAML files
ALLOWED_TYPES = {"yes_no", "yes_no_unknown", "one_of", "multi", "text", "choice"}

# Valid answer sets for yes/no and yes/no/unknown questions
VALID_YESNO = {"yes", "no"}
VALID_YNU = {"yes", "no", "unknown"}

# Move logger to module level to improve performance
logger = logging.getLogger(__name__)


def options_for(question: dict[str, Any]) -> list[dict[str, str]]:
    """Return normalized options for a question for rendering/validation.

    Converts question configuration into a standardized list of option dictionaries
    with 'value' and 'label' keys. Handles yes/no, yes_no_unknown, and custom
    option types.

    Args:
        question: Question dictionary with 'type' and optional 'options' or
            'map_to_flags' fields.

    Returns:
        List of option dictionaries, each with 'value' and 'label' keys.
        Empty list if no options are available.

    Example:
        >>> q = {"type": "yes_no"}
        >>> options_for(q)
        [{'value': 'yes', 'label': 'Yes'}, {'value': 'no', 'label': 'No'}]
    """
    question_type = question.get("type", "one_of")
    if question_type == "yes_no":
        return [{"value": "yes", "label": LABEL_YES}, {"value": "no", "label": LABEL_NO}]
    if question_type == "yes_no_unknown":
        return [
            {"value": "yes", "label": LABEL_YES},
            {"value": "no", "label": LABEL_NO},
            {"value": "unknown", "label": LABEL_UNKNOWN},
        ]
    options = question.get("options")
    if isinstance(options, list) and options:
        out: list[dict[str, str]] = []
        for it in options:
            if isinstance(it, dict) and "value" in it:
                out.append(
                    {
                        "value": str(it.get("value")),
                        "label": str(it.get("label") or it.get("value")),
                    }
                )
            else:
                out.append({"value": str(it), "label": str(it)})
        return out
    mtf = question.get("map_to_flags") or {}
    if isinstance(mtf, dict) and mtf:
        return [{"value": str(k), "label": str(k)} for k in mtf]
    return []


def _sanitize_multi(
    value: str | int | float | list[str | int | float] | None, options: list[str]
) -> list[str] | None:
    """Normalize multi-select answers and drop values not in options.

    Args:
        value: Answer value (string, number, list, or None).
        options: List of valid option strings.

    Returns:
        List of valid option strings, or None if no valid options found.
    """
    if value is None:
        return None
    vals = value if isinstance(value, list) else [value]
    cleaned = [str(v) for v in vals if str(v) in options]
    return cleaned or None


def _sanitize_one_of(value: str | int | float | None, options: list[str]) -> str | None:
    """Return the option if it exists; otherwise None.

    Args:
        value: Answer value to validate (string, number, or None).
        options: List of valid option strings.

    Returns:
        Normalized option string if valid, None otherwise.
    """
    normalized_value = str(value)
    return normalized_value if normalized_value in options else None


def _sanitize_yes_no(value: str | int | float | bool | None, valid_set: set[str]) -> str | None:
    """Normalize yes/no(/unknown) answers to lowercase tokens.

    Args:
        value: Answer value to normalize (string, number, bool, or None).
        valid_set: Set of valid lowercase tokens (e.g., {'yes', 'no'}).

    Returns:
        Lowercase normalized value if valid, None otherwise.
    """
    normalized_value = str(value).strip().lower()
    return normalized_value if normalized_value in valid_set else None


def _sanitize_text(value: str | int | float | None) -> str | None:
    """Sanitize text input: remove control chars and limit length.

    Does NOT escape HTML (handled at presentation layer).

    Args:
        value: Text value to sanitize (string, number, or None).

    Returns:
        Sanitized text string (max MAX_TEXT_LENGTH chars), or None if empty after sanitization.
    """
    text_value = str(value)
    # Remove control characters (except newlines/tabs)
    text_value = "".join(char for char in text_value if char == "\n" or char == "\t" or char >= " ")
    text_value = text_value[:MAX_TEXT_LENGTH]
    return text_value if text_value else None


def sanitize_answer(question: dict, value: Any) -> str | list[str] | None:
    """Normalize and validate a single answer for a question.

    Sanitizes user input based on question type, ensuring values conform to
    expected formats and removing invalid entries.

    Args:
        question: Question dictionary with 'type' field.
        value: Raw answer value from user input.

    Returns:
        Sanitized answer value:
            - For multi: List of valid option strings, or None if empty.
            - For one_of/choice: Single valid option string, or None if invalid.
            - For yes_no/yes_no_unknown: Lowercase canonical value ('yes', 'no', 'unknown').
            - For text: Sanitized string (max 2k chars), or None if empty.

    Example:
        >>> q = {"type": "yes_no"}
        >>> sanitize_answer(q, "YES")
        'yes'
        >>> sanitize_answer(q, "invalid")
        None
        >>> q_multi = {"type": "multi", "options": [{"value": "a"}, {"value": "b"}]}
        >>> sanitize_answer(q_multi, ["a", "b"])
        ['a', 'b']
    """
    question_type = str(question.get("type", "one_of"))
    options = [o["value"] for o in options_for(question)]

    if question_type == "multi":
        return _sanitize_multi(value, options)
    if question_type in ("one_of", "choice"):
        return _sanitize_one_of(value, options)
    if question_type == "yes_no":
        return _sanitize_yes_no(value, VALID_YESNO)
    if question_type == "yes_no_unknown":
        return _sanitize_yes_no(value, VALID_YNU)
    # Free text
    return _sanitize_text(value)


def build_id2q(questions_doc: QuestionnaireDoc) -> dict[str, dict]:
    """Build mapping question_id -> question dict.

    Flattens the hierarchical question structure (groups -> questions) into a
    flat dictionary keyed by question ID for efficient lookup.

    Args:
        questions_doc: Questionnaire document with 'groups' list, each containing
            'questions' list.

    Returns:
        Dictionary mapping question IDs to their question dictionaries.
    """
    id_to_question: dict[str, dict] = {}
    for group in questions_doc.get("groups", []) or []:
        if not isinstance(group, dict):
            continue
        for question in group.get("questions", []) or []:
            if isinstance(question, dict) and question.get("id"):
                id_to_question[str(question["id"])] = question
    return id_to_question


def sanitize_answers_dict(
    questions_doc: QuestionnaireDoc, raw_answers: dict[str, Any]
) -> UserAnswers:
    """Return sanitized answers dict keyed by question id.

    - Drops answers for unknown question ids.
    - Applies sanitize_answer per question.
    - Keeps only answers with non-None sanitized values.

    Args:
        questions_doc: Questionnaire document with groups and questions.
        raw_answers: Raw user answers dictionary (may contain invalid values).

    Returns:
        Sanitized answers dictionary with only valid, non-None values.

    Example:
        >>> doc = {"groups": [{"questions": [{"id": "Q1", "type": "yes_no"}]}]}
        >>> raw = {"Q1": "YES", "Q2": "invalid", "Q1": "yes"}
        >>> sanitize_answers_dict(doc, raw)
        {'Q1': 'yes'}
    """
    id2q = build_id2q(questions_doc)
    cleaned: UserAnswers = {}
    for qid, raw in (raw_answers or {}).items():
        q = id2q.get(str(qid))
        if not q:
            continue
        sanitized = sanitize_answer(q, raw)
        if sanitized is not None:
            cleaned[str(qid)] = sanitized
    return cleaned


def _emit_flags_for_question(question: dict, answer: str | list[str], flags_acc: set[Flag]) -> None:
    """Emit flags based on question answer.

    Handles both set_flags_on (for yes/no questions) and map_to_flags
    (for one_of/multi questions) mappings.

    Args:
        question: Question dictionary with optional 'set_flags_on' or 'map_to_flags' keys.
        answer: Answer value (string for single answers, list for multi-select).
        flags_acc: Set to accumulate emitted flags (modified in-place).

    Example:
        >>> question = {"set_flags_on": {"yes": ["flag.provider"]}}
        >>> flags = set()
        >>> _emit_flags_for_question(question, "yes", flags)
        >>> flags
        {'flag.provider'}
    """
    # Handle yes/no questions with set_flags_on mapping
    if "set_flags_on" in question:
        base_mapping = question.get("set_flags_on") or {}
        normalized_mapping = {str(k).strip().lower(): v for k, v in base_mapping.items()}
        # Add YAML-boolean synonyms: true->yes, false->no if missing
        if "true" in normalized_mapping and "yes" not in normalized_mapping:
            # Ignore invalid alias entry types
            normalized_mapping["yes"] = normalized_mapping["true"]
        if "false" in normalized_mapping and "no" not in normalized_mapping:
            normalized_mapping["no"] = normalized_mapping["false"]
        answer_key = str(answer).strip().lower()
        for flag in normalized_mapping.get(answer_key, []):
            flags_acc.add(flag)

    # Handle one_of/multi questions with map_to_flags
    if "map_to_flags" in question:
        mapping = question["map_to_flags"] or {}
        answers = answer if isinstance(answer, list) else [answer]

        for ans_value in answers:
            for flag in mapping.get(str(ans_value), []):
                flags_acc.add(flag)


def _is_visible(question: dict, flags_so_far: set[Flag]) -> bool:
    """Check if question should be visible based on current flags.

    Evaluates the 'show_if' condition against current flags. If no condition
    is specified, question is always visible. On parsing/evaluation errors,
    defaults to showing the question (fail-open behavior).

    Args:
        question: Question dictionary with optional 'show_if' condition.
        flags_so_far: Set of flags currently emitted.

    Returns:
        True if question should be visible, False otherwise. Always True if
        'show_if' is missing or evaluation fails.

    Example:
        >>> question = {"id": "Q1", "show_if": {"has": "role.source"}}
        >>> _is_visible(question, {"role.source"})
        True
        >>> _is_visible(question, {"role.operator"})
        False
        >>> question_no_condition = {"id": "Q2"}
        >>> _is_visible(question_no_condition, set())
        True
    """
    condition = question.get("show_if")
    if not condition:
        return True

    try:
        ast = parse_when(condition)
        return eval_ast(ast, flags_so_far)
    except (RuleParseError, ValueError, KeyError, TypeError, AttributeError) as e:
        # C-08: Log parsing/eval error for audit trail
        logger.warning(
            "questionnaire.show_if.parse_failed",
            {
                "question_id": question.get("id"),
                "show_if_condition": condition,
                "error_type": type(e).__name__,
                "error_msg": str(e),
                "action": "showing_question_by_default",
            },
        )
        # On parsing/eval error, show question to avoid hiding fields (fail-open)
        return True


def eval_questions(questions_doc: QuestionnaireDoc, answers: UserAnswers) -> set[Flag]:
    """Evaluate questionnaire and return emitted flags.

    Processes questions sequentially, respecting visibility conditions, and emits
    flags based on answers and question configurations (set_flags_on, map_to_flags).

    Args:
        questions_doc: Questionnaire structure with groups and questions.
        answers: User answers dict, e.g. {"S1_Q1": "yes", "BIO_Q2": "1:N_post"}.

    Returns:
        Set of flags emitted by the answers.

    Example:
        >>> doc = {
        ...     "groups": [{
        ...         "questions": [{
        ...             "id": "Q1",
        ...             "type": "yes_no",
        ...             "set_flags_on": {"yes": ["flag.provider"]}
        ...         }]
        ...     }]
        ... }
        >>> answers = {"Q1": "yes"}
        >>> eval_questions(doc, answers)
        {'flag.provider'}
    """
    emitted_flags: set[Flag] = set()
    groups = questions_doc.get("groups", []) or []

    # Process questions sequentially by group
    for group in groups:
        if not isinstance(group, dict):
            continue
        for question in group.get("questions") or []:
            if not isinstance(question, dict):
                continue
            question_id = question.get("id")

            # Skip if no ID or no answer provided
            if not question_id or question_id not in answers:
                continue

            # Skip if question is not visible based on current flags
            if not _is_visible(question, emitted_flags):
                continue

            answer = answers[question_id]
            question_type = question.get("type", "one_of")

            # Process by question type
            if question_type == "yes_no":
                normalized = str(answer).lower()
                if normalized in VALID_YESNO:
                    _emit_flags_for_question(question, normalized, emitted_flags)

            elif question_type == "yes_no_unknown":
                normalized = str(answer).lower()
                if normalized in VALID_YNU:
                    _emit_flags_for_question(question, normalized, emitted_flags)

            elif question_type in ("one_of", "choice"):
                _emit_flags_for_question(question, answer, emitted_flags)

            elif question_type == "multi":
                multi_values = answer if isinstance(answer, list) else [answer]
                _emit_flags_for_question(question, multi_values, emitted_flags)

            elif question_type == "text":
                # For text questions, treat any non-empty answer as candidate for
                # map_to_flags/set_flags_on. Sanitization (length, empties) is
                # expected to be handled by sanitize_answers_dict at the boundary.
                if answer not in (None, ""):
                    _emit_flags_for_question(question, answer, emitted_flags)

    return emitted_flags
