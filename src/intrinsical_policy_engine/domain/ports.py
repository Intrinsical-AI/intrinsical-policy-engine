# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Evidence-quality boundary used when sealing snapshots."""

from pathlib import Path
from typing import Protocol


class QualityPort(Protocol):
    """Classify a snapshot file and explain a rejected result."""

    def diagnose_file(self, path: Path) -> tuple[bool, str]:
        """Return whether the file is ready and a diagnostic reason."""
        ...
