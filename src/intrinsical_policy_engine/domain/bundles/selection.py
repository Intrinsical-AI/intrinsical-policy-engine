# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""One selection traversal for rendering and coverage, including parent guards."""

from collections.abc import Iterator
from pathlib import PurePosixPath

from intrinsical_policy_engine.domain.bundles.models import BundleNode, BundleProfile
from intrinsical_policy_engine.domain.conditions import evaluate_condition


def selected_nodes(profile: BundleProfile, flags: set[str]) -> Iterator[tuple[BundleNode, str]]:
    if not evaluate_condition(profile.when, flags):
        return

    def visit(
        nodes: tuple[BundleNode, ...], parent: PurePosixPath
    ) -> Iterator[tuple[BundleNode, str]]:
        for node in nodes:
            if not evaluate_condition(node.when, flags):
                continue
            part = node.target if node.kind == "copy" else node.name
            if part is None:
                raise ValueError(f"Missing destination for node {node.id}")
            path = parent / part
            if path.is_absolute() or ".." in path.parts:
                raise ValueError(f"Unsafe output path: {path}")
            yield node, path.as_posix()
            if node.kind == "dir":
                yield from visit(node.children, path)

    root = PurePosixPath(profile.root_dir)
    if root.is_absolute() or ".." in root.parts:
        raise ValueError(f"Unsafe profile root: {root}")
    yield from visit(profile.nodes, root)
