# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pure ownership checks for selected artifact paths, before any IO begins."""

from dataclasses import dataclass, field
from pathlib import PurePosixPath

from intrinsical_policy_engine.domain.snapshot_contract import CONTROL_PATHS, canonical_path

ENGINE_NAMESPACES = frozenset({"_metadata", "evidence", "exports", "backlogs"})
ENGINE_FILES = CONTROL_PATHS | {"evidence.zip", "compliance.ics"}


class OutputCollisionError(ValueError):
    """Two producers claim incompatible paths in the same snapshot."""


def require_authored_path(path: str) -> None:
    """Pack artifacts cannot claim namespaces managed by the engine."""
    canonical_path(path)
    first = PurePosixPath(path).parts[0]
    if first in ENGINE_NAMESPACES or first in ENGINE_FILES:
        raise OutputCollisionError(f"Output collision: pack path is reserved by the engine: {path}")


@dataclass
class OutputInventory:
    files: dict[str, str] = field(default_factory=dict)
    directories: set[str] = field(default_factory=set)

    def _parents(self, path: str) -> set[str]:
        return {parent.as_posix() for parent in PurePosixPath(path).parents if str(parent) != "."}

    def _check_ancestors(self, path: str, owner: str) -> set[str]:
        canonical_path(path)
        parents = self._parents(path)
        for parent in sorted(parents):
            if parent in self.files:
                raise OutputCollisionError(
                    f"Output collision: {path} ({owner}) descends from file {parent} "
                    f"({self.files[parent]})"
                )
        return parents

    def add_file(self, path: str, owner: str) -> None:
        parents = self._check_ancestors(path, owner)
        if path in self.files:
            raise OutputCollisionError(
                f"Output collision: {path} claimed by {self.files[path]} and {owner}"
            )
        if path in self.directories:
            raise OutputCollisionError(f"Output collision: {path} is both a directory and {owner}")
        self.files[path] = owner
        self.directories.update(parents)

    def add_directory(self, path: str, owner: str) -> None:
        parents = self._check_ancestors(path, owner)
        if path in self.files:
            raise OutputCollisionError(
                f"Output collision: {path} is both {self.files[path]} and directory {owner}"
            )
        self.directories.update(parents | {path})
