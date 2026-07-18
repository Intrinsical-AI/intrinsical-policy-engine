# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Adapters implementing graph construction ports.

This module contains implementations that connect abstract graph ports
to concrete dependencies (like the rule engine).
"""

from __future__ import annotations


class RuleEngineFlagExtractor:
    """FlagExtractor implementation using rule_engine.analyze_when.

    This adapter wraps the existing rule engine to conform to the
    FlagExtractor protocol defined in src/domain/graph/ports.py.
    """

    def extract(self, when_expr: str | dict | None) -> tuple[set[str], set[str]]:
        """Extract flags using the rule engine's analyze_when function.

        Args:
            when_expr: A 'when' condition expression (string DSL or dict)

        Returns:
            Tuple of (exact_flags, prefix_patterns)
        """
        if when_expr is None:
            return set(), set()

        from intrinsical_policy_engine.domain.services.rule_engine import analyze_when

        return analyze_when(when_expr)
