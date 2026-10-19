# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""CLI commands package with lazy loading support.

Each command module exports a `register(subparsers)` function that:
1. Adds its subparser to the parent
2. Sets a handler function via set_defaults(handler=...)

This allows lazy loading of dependencies - heavy imports only happen
when the command is actually invoked, not at CLI startup.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import argparse


def register_all_commands(subparsers: argparse._SubParsersAction) -> None:
    """Register the assessment, export, integrity, and authoring commands."""
    from intrinsical_policy_engine.app.cli.commands import core

    core.register(subparsers)
