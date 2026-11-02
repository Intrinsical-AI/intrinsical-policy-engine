# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pure contexts for the retained document and backlog renderers."""

from collections.abc import Callable
from typing import Any

from intrinsical_policy_engine.domain.bundles.backlog_config import split_actions
from intrinsical_policy_engine.domain.bundles.context import EvalContext
from intrinsical_policy_engine.domain.bundles.models import BacklogConfig

ContextBuilder = Callable[[EvalContext], dict[str, Any]]


def default_builder(ctx: EvalContext) -> dict[str, Any]:
    return {**ctx.plan, **ctx.extras}


def _split(ctx: EvalContext, split_id: str) -> dict[str, Any]:
    base = default_builder(ctx)
    config = ctx.extras.get("backlog_config")
    if not isinstance(config, BacklogConfig):
        raise ValueError("Backlog context requires a compiled BacklogConfig")
    grouped = split_actions(base.get("actions_meta", []), config)
    if split_id not in grouped:
        raise ValueError(f"unknown backlog split id: {split_id}")
    base["actions_meta"] = grouped[split_id]
    return base


def engineering_backlog_builder(ctx: EvalContext) -> dict[str, Any]:
    return _split(ctx, "engineering")


def legal_backlog_builder(ctx: EvalContext) -> dict[str, Any]:
    return _split(ctx, "governance")


# "audit_trail", "backlog" and "calendar" are accepted pack values that render
# exactly like "default".
BUILDERS: dict[str, ContextBuilder] = {
    "default": default_builder,
    "audit_trail": default_builder,
    "backlog": default_builder,
    "engineering_backlog": engineering_backlog_builder,
    "legal_backlog": legal_backlog_builder,
    "calendar": default_builder,
}


def get_builder(name: str) -> ContextBuilder:
    if name not in BUILDERS:
        raise ValueError(f"Unknown context builder: {name}")
    return BUILDERS[name]
