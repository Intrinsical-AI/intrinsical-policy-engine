# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Resolve all selected producers to a closed output inventory before staging."""

from collections.abc import Collection
from typing import Any

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import CompiledPack
from intrinsical_policy_engine.domain.bundles.models import BundleProfile
from intrinsical_policy_engine.domain.bundles.selection import selected_nodes
from intrinsical_policy_engine.domain.output_inventory import (
    ICS_FILE,
    OutputInventory,
    require_authored_path,
)
from intrinsical_policy_engine.domain.snapshot_contract import REQUIRED_METADATA


def _filesystem_outputs(
    pack: CompiledPack,
    profiles: dict[str, BundleProfile],
    plan: dict[str, Any],
    inventory: OutputInventory,
) -> None:
    for profile in profiles.values():
        for node, relative in selected_nodes(profile, set(plan["flags"])):
            require_authored_path(relative)
            owner = f"profile {profile.id}, node {node.id}"
            if node.kind == "dir":
                inventory.add_directory(relative, owner)
            else:
                inventory.add_file(relative, owner)
    prefix = pack.layout.manifest.templates_dir
    for name in ("README.md", "LEGAL_NOTICE.md"):
        template = f"{prefix}/artifacts/core/{name}.j2"
        if template in pack.layout.source_bytes and name not in inventory.files:
            inventory.add_file(name, f"core template {template}")
    inventory.add_file("_metadata/actions.json", "action metadata")
    for split in pack.backlog_config.splits:
        inventory.add_file(f"backlogs/{split.filename}", f"backlog {split.id}")
    if plan.get("due_hints"):
        inventory.add_file(ICS_FILE, "calendar")


def plan_outputs(
    pack: CompiledPack,
    profiles: dict[str, BundleProfile],
    plan: dict[str, Any],
    targets: Collection[str],
    evidence_names: Collection[str],
) -> OutputInventory:
    inventory = OutputInventory()
    for path in sorted(REQUIRED_METADATA):
        inventory.add_file(path, "assessment")
    for name in ("evidence_manifest", "evidence_quality"):
        inventory.add_file(f"_metadata/{name}.json", "evidence metadata")
    for name in evidence_names:
        inventory.add_file(f"evidence/{name}", "supplied evidence")
    if evidence_names:
        inventory.add_file("evidence.zip", "evidence archive")
    if "filesystem" in targets:
        _filesystem_outputs(pack, profiles, plan, inventory)
    for target in targets:
        if target == "filesystem":
            continue
        for name in ("requests.ndjson", "index.json"):
            inventory.add_file(f"exports/{target}/{name}", f"{target} exporter")
        if target == "asana":
            inventory.add_file("exports/asana/import.csv", "Asana import")
    return inventory
