# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Ports (interfaces) for graph construction dependencies.

Currently only :class:`FlagExtractor` is used by the graph builder.
"""

from __future__ import annotations

from typing import Protocol


class FlagExtractor(Protocol):
    """Protocol for extracting flags from 'when' condition expressions.

    This abstraction decouples the graph builder from the rule engine's
    parsing implementation, enabling:
    - Unit testing with mock extractors
    - Alternative DSL implementations
    - Clear dependency boundaries
    """

    def extract(self, when_expr: str | dict | None) -> tuple[set[str], set[str]]:
        """Extract flags and prefix patterns from a 'when' expression.

        Args:
            when_expr: A 'when' condition in string DSL or dict DSL format.
                       None means "always true" (no flags).

        Returns:
            Tuple of (exact_flags, prefix_patterns).
        """
        ...
