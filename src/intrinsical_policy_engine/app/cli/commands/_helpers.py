# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Shared validated inputs for assess and export."""

import hashlib
import sys
from argparse import Namespace
from collections.abc import Sequence
from pathlib import Path

from yaml import YAMLError

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)
from intrinsical_policy_engine.adapters.logging import StructuredLogger
from intrinsical_policy_engine.app.cli.diagnostics import DocumentError, format_problem
from intrinsical_policy_engine.common.document_io import parse_document
from intrinsical_policy_engine.domain.exceptions import ContractValidationError
from intrinsical_policy_engine.domain.input_models import AssessmentInput
from intrinsical_policy_engine.domain.services.contract_validation import Problem


def report_warnings(problems: Sequence[Problem], logger: StructuredLogger | None) -> None:
    for problem in problems:
        sys.stderr.write(f"Warning: {format_problem(problem.to_dict())}\n")
    if problems and logger is not None:
        logger.warning(
            "pack.warnings",
            {"count": len(problems), "problem_codes": sorted({p.code for p in problems})},
        )


def load_inputs(args: Namespace):
    pack = YamlContractsAdapter().compile(args.contracts)
    if args.strict and pack.problems:
        raise ContractValidationError(
            "Pack warnings rejected by --strict", [problem.to_dict() for problem in pack.problems]
        )
    report_warnings(pack.problems, args.logger)
    try:
        data = Path(args.answers).read_bytes()
        raw = parse_document(data.decode("utf-8"), Path(args.answers).suffix)
        if not isinstance(raw, dict):
            raise ValueError("Assessment input must be a mapping")
        AssessmentInput.model_validate(raw)
    except (OSError, ValueError, UnicodeError, YAMLError) as exc:
        raise DocumentError("Input", args.answers, exc) from exc
    logger = args.logger
    return pack, raw, logger, hashlib.sha256(data).hexdigest()
