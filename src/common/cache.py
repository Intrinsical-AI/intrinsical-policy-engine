# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Unified caching strategy with semantic decorators.

This module provides a consistent caching approach across the codebase,
replacing fragmented lru_cache usage with semantic, configurable decorators.
"""

from collections.abc import Callable
from functools import lru_cache
from typing import Any, ParamSpec, Protocol, TypeVar, cast

# Unified cache size constants
# Small: For lightweight operations (parsing, simple lookups)
CACHE_SIZE_SMALL = 128

# Medium: For moderate operations (rules, assessments)
CACHE_SIZE_MEDIUM = 512

# Large: For heavy operations (file operations, complex derivations)
CACHE_SIZE_LARGE = 1024

# Unlimited: For operations that benefit from unbounded cache
CACHE_SIZE_UNLIMITED = None

P = ParamSpec("P")
R = TypeVar("R")
R_co = TypeVar("R_co", covariant=True)


class CachedFunction(Protocol[P, R_co]):
    """Protocol for functions decorated with lru_cache."""

    def __call__(self, *args: P.args, **kwargs: P.kwargs) -> R_co:
        """Execute the cached function."""

    def cache_clear(self) -> None:
        """Clear the underlying LRU cache."""

    def cache_info(self) -> Any:
        """Return cache statistics (hits/misses/etc.)."""


def cached_small(func: Callable[P, R]) -> CachedFunction[P, R]:
    """Cache decorator for small, lightweight operations.

    Use for:
    - Simple string parsing
    - Basic lookups
    - Quick transformations

    Cache size: 128 entries
    """
    return cast(CachedFunction[P, R], lru_cache(maxsize=CACHE_SIZE_SMALL)(func))


def cached_medium(func: Callable[P, R]) -> CachedFunction[P, R]:
    """Cache decorator for moderate-cost operations.

    Use for:
    - Rule parsing and evaluation
    - Assessment computations
    - Template rendering

    Cache size: 512 entries
    """
    return cast(CachedFunction[P, R], lru_cache(maxsize=CACHE_SIZE_MEDIUM)(func))


def cached_large(func: Callable[P, R]) -> CachedFunction[P, R]:
    """Cache decorator for expensive operations.

    Use for:
    - File I/O operations
    - Complex derivations
    - Heavy computations

    Cache size: 1024 entries
    """
    return cast(CachedFunction[P, R], lru_cache(maxsize=CACHE_SIZE_LARGE)(func))


def cached_unlimited(func: Callable[P, R]) -> CachedFunction[P, R]:
    """Cache decorator for unbounded caching.

    Use for:
    - Static data that never changes
    - Reference data
    - Constant lookups

    Cache size: Unlimited
    """
    return cast(CachedFunction[P, R], lru_cache(maxsize=CACHE_SIZE_UNLIMITED)(func))


def cached(
    maxsize: int | None = CACHE_SIZE_MEDIUM,
) -> Callable[[Callable[P, R]], CachedFunction[P, R]]:
    """Configurable cache decorator.

    Args:
        maxsize: Maximum cache size (None for unlimited)

    Returns:
        Decorated function with LRU cache

    Example:
        @cached(maxsize=256)
        def expensive_operation(x: int) -> int:
            return x ** 2
    """

    def decorator(func: Callable[P, R]) -> CachedFunction[P, R]:
        return cast(CachedFunction[P, R], lru_cache(maxsize=maxsize)(func))

    return decorator


def clear_all_caches(*funcs: Any) -> None:
    """Clear caches for multiple functions at once.

    Args:
        *funcs: Functions whose caches should be cleared

    Example:
        clear_all_caches(parse_when, evaluate_rule, compute_assessment)
    """
    for func in funcs:
        if hasattr(func, "cache_clear"):
            func.cache_clear()


def get_cache_info(*funcs: Any) -> dict[str, dict]:
    """Get cache statistics for multiple functions.

    Args:
        *funcs: Functions to get cache info for

    Returns:
        Dictionary mapping function names to cache info

    Example:
        >>> info = get_cache_info(parse_when, evaluate_rule)
        >>> print(info['parse_when'])
        {'hits': 100, 'misses': 10, 'maxsize': 512, 'currsize': 10}
    """
    result = {}
    for func in funcs:
        if hasattr(func, "cache_info"):
            info = func.cache_info()
            result[getattr(func, "__name__", str(func))] = {
                "hits": info.hits,
                "misses": info.misses,
                "maxsize": info.maxsize,
                "currsize": info.currsize,
            }
    return result


# Backwards compatibility aliases (for gradual migration)
CACHE_MAXSIZE_ASSESSMENT = CACHE_SIZE_MEDIUM
CACHE_MAXSIZE_QUESTIONS = CACHE_SIZE_MEDIUM
CACHE_MAXSIZE_RULES = CACHE_SIZE_MEDIUM
CACHE_MAXSIZE_DERIVATIONS = CACHE_SIZE_LARGE
