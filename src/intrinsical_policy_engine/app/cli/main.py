# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""CLI entry point for pack authoring, assessment, export, and sealing."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from time import perf_counter_ns

from jinja2 import TemplateError
from yaml import YAMLError

from intrinsical_policy_engine.adapters.logging import FsLogger
from intrinsical_policy_engine.app.cli.commands import core
from intrinsical_policy_engine.app.cli.diagnostics import format_error
from intrinsical_policy_engine.app.config.paths import normalize_log_jsonl_path
from intrinsical_policy_engine.common.constants import CANONICAL_ENGINE_VERSION
from intrinsical_policy_engine.domain.exceptions import ContractValidationError, EngineError


def _logger(args: argparse.Namespace) -> FsLogger | None:
    protected = [
        Path(value)
        for name in (
            "contracts",
            "answers",
            "config",
            "evidence_dir",
            "out",
            "export_dir",
            "output",
        )
        if (value := getattr(args, name, None)) is not None
    ]
    path = normalize_log_jsonl_path(getattr(args, "log_jsonl", None), protected)
    return FsLogger(path, command=args.command) if path is not None else None


def _failed_log(args: argparse.Namespace, started: int, error: Exception) -> None:
    if args.logger is None:
        return
    data: dict[str, object] = {
        "exit_code": 1,
        "duration_ms": (perf_counter_ns() - started) // 1_000_000,
        "error_type": type(error).__name__,
    }
    if isinstance(error, ContractValidationError):
        data["problem_codes"] = sorted({problem["code"] for problem in error.problems})
    try:
        args.logger.error("command.failed", data)
    except (OSError, ValueError, TypeError) as log_error:
        sys.stderr.write(f"Operational logging also failed: {log_error}\n")


def _run(args: argparse.Namespace) -> int:
    started = perf_counter_ns()
    args.logger = None
    completed = False
    try:
        args.logger = _logger(args)
        if args.logger is not None:
            args.logger.info("command.start")
        result = args.handler(args)
        exit_code = int(result) if result is not None else 0
        completed = True
        if args.logger is not None:
            data = {
                "exit_code": exit_code,
                "duration_ms": (perf_counter_ns() - started) // 1_000_000,
            }
            if exit_code == 0:
                args.logger.info("command.complete", data)
            else:
                args.logger.error("command.failed", data)
        return exit_code
    except (
        ValueError,
        TypeError,
        OSError,
        UnicodeError,
        YAMLError,
        EngineError,
        TemplateError,
    ) as error:
        sys.stderr.write(format_error(error, args))
        if completed:
            sys.stderr.write(
                "The command finished before logging failed; output may already exist.\n"
            )
        _failed_log(args, started, error)
        return 1


def main() -> int:
    """Main CLI entry point with unified subparser architecture."""
    parser = argparse.ArgumentParser(
        prog="ipe",
        description="Framework-neutral policy assessment and export tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ipe lint --contracts frameworks/starter
  ipe export --contracts frameworks/starter \\
    --answers demos/starter/basic/answers.json --out out/starter
  ipe assess --contracts frameworks/starter \\
    --answers demos/starter/basic/answers.json --out out/assessment
  ipe seal --export-dir out/starter --output out/starter.zip
  ipe inspect --contracts frameworks/starter

For more information, see: https://github.com/Intrinsical-AI/intrinsical-policy-engine
""",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {CANONICAL_ENGINE_VERSION}",
    )
    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        description="Available commands (use '<command> --help' for details)",
        metavar="<command>",
    )
    core.register(subparsers)
    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return 0
    # Every subcommand sets a handler and runs through the same diagnostics and logging.
    return _run(args)
