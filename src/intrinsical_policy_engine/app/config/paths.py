# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Path utilities and directory resolution."""

from pathlib import Path

from intrinsical_policy_engine.app.config.artifact_names import LOGS_DIR, OUT_DIR, PLANS_DIR
from intrinsical_policy_engine.app.config.constants import METADATA_DIR


def resolve_contracts_path(path: str) -> Path:
    """Resolve a caller-provided contracts path.

    Args:
        path: Path string to resolve (can be absolute or relative).

    Returns:
        The path exactly as resolved by :class:`pathlib.Path`. Relative paths
        are relative to the caller's current working directory.
    """
    return Path(path)


def resolve_answers_path(path: str | None) -> Path | None:
    """Return a caller-provided answers path when present.

    Args:
        path: Path string to resolve (can be absolute or relative), or None.

    Returns:
        Path object if a value was provided, otherwise ``None``. Relative paths
        are relative to the caller's current working directory.
    """
    if not path:
        return None
    return Path(path)


def resolve_out_dir(override: str | None) -> Path:
    """Resolve an explicit output directory without creating it."""
    return Path(override).expanduser() if override else Path.cwd() / OUT_DIR


def get_out_dir(override: str | None) -> Path:
    """Resolve and create an explicit output directory."""
    base = resolve_out_dir(override)
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
