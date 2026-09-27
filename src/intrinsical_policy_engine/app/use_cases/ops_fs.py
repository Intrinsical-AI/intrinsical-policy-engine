# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Filesystem utilities shared across CLI operations."""

from __future__ import annotations

import contextlib
import shutil
from collections.abc import Iterable
from pathlib import Path

from intrinsical_policy_engine.app.config.artifact_names import LOGS_DIR
from intrinsical_policy_engine.app.config.constants import MAX_DIRECTORY_SEARCH_DEPTH


def find_repo_root(start: Path) -> Path | None:
    """Find repository root by looking for pyproject.toml or .git upwards.
    Returns None if not found within a reasonable number of levels.
    """
    cur = start
    for _ in range(MAX_DIRECTORY_SEARCH_DEPTH):
        if (cur / "pyproject.toml").exists() or (cur / ".git").exists():
            return cur
        if cur.parent == cur:
            break
        cur = cur.parent
    return None


essential_names = {LOGS_DIR}


def clean_out_dir(out_dir: Path, keep: Iterable[Path] | None = None) -> None:
    """Safely remove contents under out_dir except keep paths.
    Safeguards:
    - Create directory if needed.
    - Skip if out_dir equals repo root or its parent.
    - Skip if out_dir is too shallow (<=3 parts).
    - Always preserve `logs/` subdirectory.
    """
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError:
        return

    try:
        base = out_dir.resolve()
    except OSError:
        base = out_dir

    repo = find_repo_root(base)
    if repo and (base == repo or base == repo.parent):
        return
    if len(base.parts) <= 3:
        return

    keep_set: set[Path] = set()
    for p in list(keep or []):
        try:
            rp = Path(p).resolve()
        except OSError:
            continue
        if str(rp).startswith(str(base)):
            keep_set.add(rp)

    for child in list(out_dir.iterdir()):
        try:
            cr = child.resolve()
        except OSError:
            cr = child
        if cr in keep_set:
            continue
        if any(rp.is_relative_to(cr) for rp in keep_set):
            continue
        if child.is_dir() and (child.name in essential_names):
            continue
        if child.is_dir():
            shutil.rmtree(child, ignore_errors=True)
        else:
            with contextlib.suppress(OSError):
                child.unlink()
