# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Path utilities and directory resolution."""

import os
from pathlib import Path

from src.app.config.artifact_names import LOGS_DIR, OUT_DIR, PLANS_DIR
from src.app.config.constants import METADATA_DIR


def repo_root() -> Path:
    """Return repository root (two levels up from this module)."""
    return Path(__file__).resolve().parents[3]


def resolve_contracts_path(path: str) -> Path:
    """Resolve a contracts path relative to repo root when needed.

    Args:
        path: Path string to resolve (can be absolute or relative).

    Returns:
        Resolved Path object. If path exists as-is, returns it. Otherwise,
        tries resolving relative to repo root. Returns original path if neither exists.
    """
    candidate_path = Path(path)
    if candidate_path.exists():
        return candidate_path
    alternative_path = repo_root() / path
    return alternative_path if alternative_path.exists() else candidate_path


def resolve_answers_path(path: str | None) -> Path | None:
    """Return an answers path if it exists locally or relative to repo root.

    Args:
        path: Path string to resolve (can be absolute or relative), or None.

    Returns:
        Resolved Path object if path exists, None if path is None.
        Tries local path first, then repo-relative path.
    """
    if not path:
        return None
    candidate_path = Path(path)
    if candidate_path.exists():
        return candidate_path
    alternative_path = repo_root() / path
    return alternative_path if alternative_path.exists() else candidate_path


def get_out_dir(override: str | None) -> Path:
    """Resolve the output directory honoring CLI override and env var."""
    env = os.getenv("IPE_OUT_DIR") or os.getenv("LEXOPS_OUT_DIR")
    if override:
        base = Path(override)
    elif env:
        base = Path(env)
    else:
        base = Path.cwd() / OUT_DIR
    base.mkdir(parents=True, exist_ok=True)
    return base


def ensure_dir(path: Path) -> Path:
    """Ensure a directory exists and return it.

    Args:
        path: Directory path to ensure exists.

    Returns:
        The same Path object (for chaining).
    """
    path.mkdir(parents=True, exist_ok=True)
    return path


def logs_dir(out_dir: Path) -> Path:
    """Return the logs directory under the provided output root."""
    return ensure_dir(out_dir / LOGS_DIR)


def plans_dir(out_dir: Path) -> Path:
    """Return the plans directory under the provided output root."""
    return ensure_dir(out_dir / PLANS_DIR)


def normalize_log_jsonl_path(path: str | None, out_dir: Path) -> Path | None:
    """Normalize log-jsonl path; keep logs under _metadata/logs when inside out_dir."""
    if not path:
        return None

    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = out_dir / candidate

    try:
        candidate.relative_to(out_dir)
    except ValueError:
        return candidate

    metadata_logs = ensure_dir(out_dir / METADATA_DIR / "logs")
    try:
        candidate.relative_to(metadata_logs)
        return candidate
    except ValueError:
        return metadata_logs / "engine.jsonl"
