# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""CLI entry point for Intrinsical Policy Engine assessment.

Architecture:
- Uses subparsers for command groups (validate, ops, dev)
- Lazy loading via handler pattern to avoid slow startup
- All commands use unified handler pattern (no legacy paths)

Usage:
    ipe <command> [options]
    ipe validate <subcommand> [options]
    ipe ops <subcommand> [options]
    ipe dev <subcommand> [options]  # Hidden unless IPE_DEV_MODE=1
"""

from __future__ import annotations

import argparse
import sys

from intrinsical_policy_engine.common.constants import CANONICAL_ENGINE_VERSION

# Version for --version flag
__version__ = CANONICAL_ENGINE_VERSION


def main() -> int:
    """Main CLI entry point with unified subparser architecture."""
    from intrinsical_policy_engine.app.config.environment import (
        EnvironmentConfigurationError,
        load_cli_environment,
    )

    try:
        cli_environment = load_cli_environment()
    except EnvironmentConfigurationError as exc:
        sys.stderr.write(f"Environment configuration error: {exc}\n")
        return 2

    parser = argparse.ArgumentParser(
        prog="ipe",
        description="Framework-neutral policy assessment and export tool",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  ipe lint --contracts frameworks/starter
  ipe export --contracts frameworks/starter \\
    --answers demos/starter/basic/answers.json --out out/starter
  ipe validate all --contracts frameworks/starter
  ipe ops render --templates frameworks/starter/render \
    --plan out/starter/_metadata/summary.json --out out/rendered
  ipe seal --export-dir out/starter

For more information, see: https://github.com/Intrinsical-AI/intrinsical-policy-engine
""",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )

    # Create subparsers
    subparsers = parser.add_subparsers(
        dest="command",
        title="commands",
        description="Available commands (use '<command> --help' for details)",
        metavar="<command>",
    )

    # Register all command groups with lazy loading
    from intrinsical_policy_engine.app.cli.commands import register_all_commands

    register_all_commands(subparsers, show_dev=cli_environment.dev_mode)
    parser.set_defaults(_ipe_environment=cli_environment)

    # Ensure predicates are registered globally
    from intrinsical_policy_engine.domain.bundles.predicates import register_core_predicates

    register_core_predicates()

    # Parse arguments
    args = parser.parse_args()

    # Only core commands use the shared execution-policy defaults. Other
    # command groups retain their own explicit strictness semantics.
    if hasattr(args, "strict") and args.strict is None:
        args.strict = cli_environment.strict_contracts
    if hasattr(args, "out") and args.out is None:
        args.out = str(cli_environment.out_dir or "out")

    # If no command given, show help
    if not args.command:
        parser.print_help()
        return 0

    # Execute handler (all commands now use unified handler pattern)
    if not hasattr(args, "handler"):
        sys.stderr.write(f"Error: Command '{args.command}' has no handler implementation\n")
        return 1

    try:
        handler = args.handler
        handler_result = handler(args)
        return int(handler_result) if handler_result is not None else 0
    except NotImplementedError as exc:
        sys.stderr.write(f"Error: Command '{args.command}' not yet implemented: {exc}\n")
        return 1
    except (ValueError, TypeError) as exc:
        sys.stderr.write(f"Error executing '{args.command}': {exc}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
