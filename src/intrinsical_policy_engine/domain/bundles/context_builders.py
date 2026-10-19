# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pure contexts for the retained document and backlog renderers."""

from collections.abc import Callable
from typing import Any

from intrinsical_policy_engine.domain.bundles.backlog_config import (
    get_keywords_for_split,
    split_actions,
)
from intrinsical_policy_engine.domain.bundles.context import EvalContext
from intrinsical_policy_engine.domain.bundles.models import BacklogConfig

ContextBuilder = Callable[[EvalContext], dict[str, Any]]


def default_builder(ctx: EvalContext) -> dict[str, Any]:
    return {**ctx.plan, **ctx.extras}


def audit_trail_builder(ctx: EvalContext) -> dict[str, Any]:
    return default_builder(ctx)


def backlog_builder(ctx: EvalContext) -> dict[str, Any]:
    return default_builder(ctx)


def _split(ctx: EvalContext, split_id: str) -> dict[str, Any]:
    base = default_builder(ctx)
    config = ctx.extras.get("backlog_config")
    if not isinstance(config, BacklogConfig):
        raise ValueError("Backlog context requires a compiled BacklogConfig")
    get_keywords_for_split(config, split_id)
    grouped = split_actions(base.get("actions_meta", []), config)
    base["actions_meta"] = grouped[split_id]
    return base


def engineering_backlog_builder(ctx: EvalContext) -> dict[str, Any]:
    return _split(ctx, "engineering")


def legal_backlog_builder(ctx: EvalContext) -> dict[str, Any]:
    return _split(ctx, "governance")


def calendar_builder(ctx: EvalContext) -> dict[str, Any]:
    return default_builder(ctx)


BUILDERS: dict[str, ContextBuilder] = {
    "default": default_builder,
    "audit_trail": audit_trail_builder,
    "backlog": backlog_builder,
    "engineering_backlog": engineering_backlog_builder,
    "legal_backlog": legal_backlog_builder,
    "calendar": calendar_builder,
}


def get_builder(name: str) -> ContextBuilder:
    if name not in BUILDERS:
        raise ValueError(f"Unknown context builder: {name}")
    return BUILDERS[name]
