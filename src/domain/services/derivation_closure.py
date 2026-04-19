# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Derivation closure: compute all flags reachable via recursive rules."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from src.domain.constants import MAX_DERIVATION_ITERATIONS
from src.domain.services.rule_engine import eval_ast, parse_when
from src.domain.types import ConditionAST, Flag

if TYPE_CHECKING:
    from src.domain.contract_models import RulesContract


@dataclass(frozen=True)
class DerivationResult:
    """Result of derivation closure with full audit trail.

    Attributes:
        final_flags: Complete set of flags after derivation closure.
        derived_flags: Flags that were added during derivation (not in initial set).
        derivations_applied: List of derivations that fired, each with 'when' and 'flags_set'.
        rules_evaluated: Total count of rule evaluations performed (for CONTRACTS.md §16).
        iterations: Number of iterations to reach convergence.
    """

    final_flags: frozenset[Flag]
    derived_flags: frozenset[Flag]
    derivations_applied: tuple[dict, ...] = field(default_factory=tuple)
    rules_evaluated: int = 0
    iterations: int = 0


def derive(flags: set[Flag], rules: RulesContract) -> set[Flag]:
    """Compute transitive closure of flags using derivation rules.

    Args:
        flags: Initial set of flags
        rules: RulesContract containing derivations

    Returns:
        Final set of flags after applying all derivations

    Raises:
        RuntimeError: If derivations don't converge (cycle detected)
    """
    result = derive_with_trace(flags, rules)
    return set(result.final_flags)


def derive_with_trace(flags: set[Flag], rules: RulesContract) -> DerivationResult:
    """Compute transitive closure with full audit trail.

    Like derive(), but returns a DerivationResult with:
    - derived_flags: which flags were added
    - derivations_applied: which rules fired and what they set
    - iterations: convergence count

    This supports tests/CONTRACTS.md Level 4 traceability requirements.

    Args:
        flags: Initial set of flags
        rules: RulesContract containing derivations

    Returns:
        DerivationResult with complete audit trail

    Raises:
        RuntimeError: If derivations don't converge (cycle detected)
    """
    initial_flags = frozenset(flags)
    result = set(flags)
    changed = True
    iterations = 0
    derivations_applied: list[dict] = []

    compiled: list[tuple[ConditionAST, list[Flag], object]] = []
    for d in rules.derivations:
        condition = d.when
        targets = d.flags_to_set
        try:
            ast = parse_when(condition)
        except (ValueError, KeyError, TypeError, AttributeError) as e:
            raise RuntimeError(f"Error parsing derivation when={condition!r} set={targets}") from e
        compiled.append((ast, list(targets), condition))

    max_iterations = MAX_DERIVATION_ITERATIONS
    rules_evaluated_count = 0

    while changed:
        iterations += 1

        if iterations > max_iterations:
            sample_flags = sorted(result)[:20]
            raise RuntimeError(
                f"Derivation did not converge after {max_iterations} iterations. "
                f"Current flags (sample): {sample_flags}..."
            )

        changed = False

        for ast, target_flags, original_cond in compiled:
            rules_evaluated_count += 1
            try:
                if eval_ast(ast, result):
                    newly_set = []
                    for flag in target_flags:
                        if flag not in result:
                            result.add(flag)
                            newly_set.append(flag)
                            changed = True
                    # Record this derivation if it actually set new flags
                    if newly_set:
                        derivations_applied.append(
                            {
                                "when": original_cond,
                                "flags_set": newly_set,
                            }
                        )
            except (ValueError, KeyError, TypeError, AttributeError) as e:
                raise RuntimeError(
                    f"Error evaluating derivation when={original_cond!r} set={target_flags}"
                ) from e

    derived_flags = frozenset(result) - initial_flags

    return DerivationResult(
        final_flags=frozenset(result),
        derived_flags=derived_flags,
        derivations_applied=tuple(derivations_applied),
        rules_evaluated=rules_evaluated_count,
        iterations=iterations,
    )
