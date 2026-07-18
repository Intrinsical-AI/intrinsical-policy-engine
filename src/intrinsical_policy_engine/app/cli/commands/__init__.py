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


def register_all_commands(
    subparsers: argparse._SubParsersAction, *, show_dev: bool = False
) -> None:
    """Register all command groups with lazy loading.

    Commands are organized into groups:
    - Core: lint, assess, export, seal, ui, wizard, inspect
    - Validate: contracts, evidence, templates, all
    - Ops: render
    - Dev: (hidden) build-framework, graph
    """
    # Core commands (registered inline)
    from intrinsical_policy_engine.app.cli.commands import core

    core.register(subparsers)

    # Validate group
    from intrinsical_policy_engine.app.cli.commands import validate

    validate.register(subparsers)

    # Ops group
    from intrinsical_policy_engine.app.cli.commands import ops

    ops.register(subparsers)

    # Dev group (hidden from main help)
    from intrinsical_policy_engine.app.cli.commands import dev

    dev.register(subparsers, show_dev=show_dev)
