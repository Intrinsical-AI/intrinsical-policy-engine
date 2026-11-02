# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Build a render context from the already projected assessment snapshot."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from intrinsical_policy_engine.common.constants import (
    CANONICAL_ENGINE_NAME,
    CANONICAL_ENGINE_VERSION,
)


def build_base_context(plan: dict[str, Any]) -> dict[str, Any]:
    ctx = deepcopy(plan)
    trace = ctx.get("trace", {})
    ctx["plan"] = {"hash": trace.get("plan_hash", ""), "actions": ctx.get("actions", [])}
    ctx["answers"] = dict(trace.get("answers_raw", {}).get("input", {}).get("answers", {}))
    ctx["engine"] = {"name": CANONICAL_ENGINE_NAME, "version": CANONICAL_ENGINE_VERSION}
    ctx["meta"] = {**ctx.get("meta", {}), "generated_at": ctx.get("assessment_timestamp")}
    for key in ("system", "provider", "owner", "declared_by", "approvals"):
        ctx.setdefault(key, {})
    return ctx


def build_artifact_context(
    plan: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {**deepcopy(defaults or {}), **build_base_context(plan)}
