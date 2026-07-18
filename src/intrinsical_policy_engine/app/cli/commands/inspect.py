# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""CLI command for inspecting rules and other artifacts."""

from pathlib import Path

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    YamlContractsAdapter,
)
from intrinsical_policy_engine.domain.services.rule_engine import (
    OP_ALL,
    OP_ANY,
    OP_ANY_PREFIX,
    OP_HAS,
    OP_NOT,
    parse_when,
)
from intrinsical_policy_engine.domain.types import ASTNode


def _print_ast(node: ASTNode, prefix: str = "", is_last: bool = True) -> None:
    """Recursively print AST with tree drawing characters."""
    if node is None:
        return

    # Tree drawing characters
    connector = "└── " if is_last else "├── "
    child_prefix = "    " if is_last else "│   "

    if not isinstance(node, tuple):
        print(f"{prefix}{connector}UNKNOWN: {node}")
        return

    op = node[0]
    args = node[1]

    # Leaf nodes
    if op == OP_HAS:
        print(f"{prefix}{connector}has('{args}')")
        return
    if op == OP_ANY_PREFIX:
        print(f"{prefix}{connector}any_prefix('{args}')")
        return

    # Branch nodes
    label = op.upper()
    if op == OP_ANY:
        label = "OR"
    elif op == OP_ALL:
        label = "AND"
    elif op == OP_NOT:
        label = "NOT"

    print(f"{prefix}{connector}{label}")

    # Handle children
    if op == OP_NOT:
        # Single child
        _print_ast(args, prefix + child_prefix, True)
    elif op in (OP_ANY, OP_ALL):
        # Multiple children
        count = len(args)
        for i, child in enumerate(args):
            _print_ast(child, prefix + child_prefix, i == count - 1)


def _get_item_id(item) -> str | None:
    """Get ID from Pydantic model or dict."""
    item_id = getattr(item, "id", None)
    if item_id is None and isinstance(item, dict):
        item_id = item.get("id")
    return item_id


def _find_rule_by_id(collection, rule_id: str, rule_type: str):
    """Search a collection for a rule with matching ID."""

    for item in collection:
        if _get_item_id(item) == rule_id:
            return item, rule_type
    return None, ""


def _get_attr(obj, key, default=None):
    """Get attribute from Pydantic model or dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def inspect_rule(contracts_dir: Path, rule_id: str) -> None:
    """Find a rule by ID and print its decision tree."""
    from typing import Any

    adapter = YamlContractsAdapter()
    bundle = adapter.load(str(contracts_dir))

    # Rules are expected to be loaded as typed models.
    rules = bundle.rules
    derivations = rules.derivations
    packs = rules.packs
    stops = rules.stops

    # Search for the rule in all collections
    found_rule: Any = None
    rule_type = "unknown"

    for collection, rtype in [(derivations, "derivation"), (packs, "pack"), (stops, "stop")]:
        found_rule, rule_type = _find_rule_by_id(collection, rule_id, rtype)
        if found_rule:
            break

    if not found_rule:
        print(f"❌ Rule '{rule_id}' not found in contracts.")
        return

    print(f"\n🔍 Inspecting Rule: {rule_id} ({rule_type})")
    print("-" * 40)

    when_clause = _get_attr(found_rule, "when")
    if not when_clause:
        print("Condition: (Always True)")
        return

    try:
        ast = parse_when(when_clause)
        _print_ast(ast, is_last=True)
    except Exception as e:  # noqa: BLE001
        print(f"❌ Failed to parse condition: {e}")
        print(f"Raw 'when': {when_clause}")

    print("-" * 40)
    if rule_type == "pack":
        print(f"Adds Actions: {_get_attr(found_rule, 'add_actions', [])}")
        if _get_attr(found_rule, "halt"):
            print("🛑 HALT: True")
    elif rule_type == "stop":
        print(f"Outcome: {_get_attr(found_rule, 'outcome')}")
    elif rule_type == "derivation":
        # rules.yml uses set_flags (list), model uses to_flag (single)
        # We try both
        set_flags = _get_attr(found_rule, "set_flags")
        if not set_flags:
            to_flag = _get_attr(found_rule, "to_flag")
            if to_flag:
                set_flags = [to_flag]
        print(f"Set Flags: {set_flags}")
