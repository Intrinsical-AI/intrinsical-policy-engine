# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Resolve user paths from the working directory without filesystem writes."""

from pathlib import Path


def normalize_log_jsonl_path(path: str | None, protected_paths: list[Path]) -> Path | None:
    if path is None:
        return None
    candidate = Path(path).absolute()
    sidecar = candidate.with_name(f".{candidate.name}.lock")
    if any(
        write_path.resolve().is_relative_to(root.resolve())
        for write_path in (candidate, sidecar)
        for root in protected_paths
    ):
        raise ValueError("Operational logs must be outside input and output paths")
    return candidate
