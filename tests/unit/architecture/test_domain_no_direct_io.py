# SPDX-License-Identifier: MPL-2.0
"""Architectural guard against infrastructure I/O leaking into domain services."""

from __future__ import annotations

import ast
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
DOMAIN_ROOT = REPO_ROOT / "src" / "intrinsical_policy_engine" / "domain"

FORBIDDEN_ATTRIBUTE_CALLS = {
    "open": "direct file handles",
    "read_bytes": "binary filesystem reads",
    "read_text": "text filesystem reads",
}
FORBIDDEN_QUALIFIED_CALLS = {
    "os.system": "shell execution",
    "subprocess.Popen": "system subprocesses",
    "subprocess.run": "system subprocesses",
    "yaml.load": "YAML loading",
    "yaml.safe_load": "YAML loading",
}


def _qualified_name(node: ast.expr) -> str | None:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        parent = _qualified_name(node.value)
        return f"{parent}.{node.attr}" if parent else node.attr
    return None


def test_domain_layer_has_no_direct_io_primitives() -> None:
    offenders: list[str] = []

    assert DOMAIN_ROOT.is_dir()
    for path in sorted(DOMAIN_ROOT.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        relative_path = path.relative_to(REPO_ROOT).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            name = _qualified_name(node.func)
            if name in FORBIDDEN_QUALIFIED_CALLS:
                offenders.append(
                    f"{relative_path}:{node.lineno}: {name} "
                    f"({FORBIDDEN_QUALIFIED_CALLS[name]} belongs in adapters/app)"
                )
            elif isinstance(node.func, ast.Attribute) and node.func.attr in (
                FORBIDDEN_ATTRIBUTE_CALLS
            ):
                offenders.append(
                    f"{relative_path}:{node.lineno}: .{node.func.attr} "
                    f"({FORBIDDEN_ATTRIBUTE_CALLS[node.func.attr]} belongs in adapters/app)"
                )
            elif isinstance(node.func, ast.Name) and node.func.id == "open":
                offenders.append(
                    f"{relative_path}:{node.lineno}: open "
                    "(direct file handles belong in adapters/app)"
                )

    assert offenders == []
