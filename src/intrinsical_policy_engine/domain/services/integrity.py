# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Separate semantic plan identity from captured-source snapshot fingerprints."""

from __future__ import annotations

import hashlib
import json

from intrinsical_policy_engine.domain.contract_models import ContractBundle

_METADATA = frozenset(
    {
        "system",
        "provider",
        "owner",
        "declared_by",
        "approvals",
        "meta",
        "export_context",
        "assessment_timestamp",
        "snapshot_format_version",
        "plan_hash",
    }
)


def compute_plan_hash(plan_data: dict) -> str:
    semantic = {
        key: value for key, value in plan_data.items() if key not in _METADATA and key != "trace"
    }
    profile = semantic.get("system_profile")
    if isinstance(profile, dict):
        semantic["system_profile"] = {key: value for key, value in profile.items() if key != "name"}
    content = json.dumps(semantic, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def compute_bundle_hash(bundle: ContractBundle) -> str:
    content = json.dumps(
        bundle.model_dump(mode="json", exclude={"path"}),
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(content.encode("utf-8")).hexdigest()
