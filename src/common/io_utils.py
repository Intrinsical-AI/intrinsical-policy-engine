# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""File I/O utility functions with consistent encoding and error handling."""

from __future__ import annotations

import logging
from pathlib import Path

from src.app.config.constants import DEFAULT_ENCODING

logger = logging.getLogger(__name__)


def read_text_safe(path: Path | str, encoding: str = DEFAULT_ENCODING) -> str | None:
    """Safely read text file with consistent encoding and error handling.

    Args:
        path: Path to the file to read
        encoding: Text encoding (defaults to UTF-8)

    Returns:
        File contents if successful, None otherwise
    """
    try:
        return Path(path).read_text(encoding=encoding)
    except (OSError, UnicodeDecodeError) as e:
        logger.warning("read_text_safe failed", {"path": str(path), "error": str(e)})
        return None


def write_text_safe(path: Path | str, data: str, encoding: str = DEFAULT_ENCODING) -> bool:
    """Safely write text file with consistent encoding and error handling.

    Args:
        path: Path to the file to write
        data: Text content to write
        encoding: Text encoding (defaults to UTF-8)

    Returns:
        True if successful, False otherwise
    """
    try:
        path_obj = Path(path)
        path_obj.parent.mkdir(parents=True, exist_ok=True)
        path_obj.write_text(data, encoding=encoding)
        return True
    except (OSError, UnicodeEncodeError) as e:
        logger.warning("write_text_safe failed", {"path": str(path), "error": str(e)})
        return False


def ensure_dir(path: Path | str) -> bool:
    """Create directory with parents if it doesn't exist.

    Args:
        path: Directory path to create

    Returns:
        True if directory exists or was created successfully, False otherwise
    """
    try:
        Path(path).mkdir(parents=True, exist_ok=True)
        return True
    except OSError as e:
        logger.warning("ensure_dir failed", {"path": str(path), "error": str(e)})
        return False


def resolve_path_safe(path: Path | str) -> Path | None:
    """Safely resolve path to absolute form.

    Args:
        path: Path to resolve

    Returns:
        Resolved absolute path if successful, None otherwise
    """
    try:
        return Path(path).resolve()
    except OSError as e:
        logger.warning("resolve_path_safe failed", {"path": str(path), "error": str(e)})
        return None
