# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Abstract filesystem interface for dependency injection.

Feedback Fix (Fase 1): Allows evidence_validator to be tested without
hitting the real filesystem, enabling faster and more reliable unit tests.

Usage:
    # Production code uses RealFileSystem by default
    problems = validate_evidence_map_integrity(bundle)

    # Test code can inject MockFileSystem
    mock_fs = MockFileSystem({"evidence/templates/provider/rms/doc.md"})
    problems = validate_evidence_map_integrity(bundle, fs=mock_fs)
"""

from pathlib import Path
from typing import Protocol


class FileExistsChecker(Protocol):
    """Protocol for checking file existence.

    Using Protocol instead of ABC for structural typing -
    any object with an 'exists' method works.
    """

    def exists(self, path: Path) -> bool:
        """Check if a path exists.

        Args:
            path: Path to check

        Returns:
            True if path exists, False otherwise
        """
        ...


class RealFileSystem:
    """Real filesystem implementation - delegates to Path.exists()."""

    def exists(self, path: Path) -> bool:
        """Check if path exists on real filesystem."""
        return path.exists()


class MockFileSystem:
    """Mock filesystem for testing.

    Allows specifying which files "exist" without touching disk.

    Example:
        mock = MockFileSystem({"evidence/rms.md", "evidence/doc.md"})
        assert mock.exists(Path("evidence/rms.md"))
        assert not mock.exists(Path("missing.md"))
    """

    def __init__(self, existing_files: set[str] | None = None):
        """Initialize with set of existing file paths.

        Args:
            existing_files: Set of path strings that should "exist".
                           Paths can be absolute or relative.
        """
        self._files: set[str] = existing_files or set()

    def exists(self, path: Path) -> bool:
        """Check if path was declared as existing.

        Matches:
        - Exact path match
        - Path ending match (for relative paths in set)
        """
        path_str = str(path)

        # Exact match
        if path_str in self._files:
            return True

        # Check if any file in our set matches the end of the path
        # This handles cases like:
        #   path = "/full/path/to/framework/evidence/templates/provider/rms/doc.md"
        #   file = "evidence/templates/provider/rms/doc.md"
        return any(path_str.endswith(f) or f.endswith(str(path.name)) for f in self._files)

    def clear(self) -> None:
        """Clear all mock files."""
        self._files.clear()
