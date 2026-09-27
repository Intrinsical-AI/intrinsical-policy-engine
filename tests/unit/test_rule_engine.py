# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.

from __future__ import annotations

import pytest

from intrinsical_policy_engine.domain.exceptions import RuleEvaluationError
from intrinsical_policy_engine.domain.services.rule_engine import eval_ast


def test_eval_ast_raises_evaluation_error_for_unknown_operator() -> None:
    with pytest.raises(RuleEvaluationError, match="Unknown AST operator: unsupported"):
        eval_ast(("unsupported", None), set())
