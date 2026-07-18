# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Exporter registry for dynamic export target management."""

from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import Any

from intrinsical_policy_engine.domain.exceptions import ExporterNotFoundError

_registry: dict[str, Callable[[], Any]] = {}


def register(name: str, factory: Callable[[], Any]) -> None:
    """Register an exporter factory under a given name.

    Registers a factory function that creates exporter instances. The factory
    is called each time get_exporter() is invoked for the given name.

    Args:
        name: The name to register the exporter under (e.g., 'filesystem', 'asana').
        factory: A callable that creates an exporter instance (no arguments).

    Example:
        >>> register("custom", lambda: MyCustomExporter())
        >>> exporter = get_exporter("custom")
    """
    _registry[name] = factory


def get_exporter(name: str) -> Any:
    """Get an exporter instance by name.

    Creates a new exporter instance by calling the factory registered for the
    given name. Each call returns a fresh instance.

    Args:
        name: The registered name of the exporter (e.g., 'filesystem', 'asana').

    Returns:
        An exporter instance created by the registered factory. The exact type
        depends on the registered factory, but all exporters implement the
        BaseExporter interface.

    Raises:
        ExporterNotFoundError: If the exporter name is not registered.

    Example:
        >>> exporter = get_exporter("filesystem")
        >>> exporter.setup(logger, config)
    """
    if name not in _registry:
        available = sorted(_registry.keys())
        raise ExporterNotFoundError(target=name, available=available)
    return _registry[name]()


def canonical_target(name: str) -> str:
    """Return `name` unchanged."""
    return str(name)


def target_name_variants(name: str) -> list[str]:
    """Return the only supported target-name variant."""
    return [str(name)]


# Default registrations
try:
    from intrinsical_policy_engine.adapters.export.filesystem.filesystem_exporter import (
        FilesystemExporter,
    )

    register("filesystem", lambda: FilesystemExporter())
except (ImportError, ModuleNotFoundError) as e:
    warnings.warn(
        f"Exporter 'filesystem' unavailable (missing dependency): {e}", RuntimeWarning, stacklevel=2
    )

try:
    from intrinsical_policy_engine.adapters.export.filesystem.filesystem_exporter import (
        FilesystemExporter as _FS,
    )

    register("jinja", lambda: _FS())
except (ImportError, ModuleNotFoundError):
    pass

try:
    from intrinsical_policy_engine.adapters.export.asana.asana_exporter import AsanaExporter

    register("asana", lambda: AsanaExporter())
except (ImportError, ModuleNotFoundError) as e:
    warnings.warn(
        f"Exporter 'asana' unavailable (optional dependency): {e}", RuntimeWarning, stacklevel=2
    )

try:
    from intrinsical_policy_engine.adapters.export.linear.linear_exporter import LinearExporter

    register("linear", lambda: LinearExporter())
except (ImportError, ModuleNotFoundError) as e:
    warnings.warn(
        f"Exporter 'linear' unavailable (optional dependency): {e}", RuntimeWarning, stacklevel=2
    )

try:
    from intrinsical_policy_engine.adapters.export.jira.jira_exporter import JiraExporter

    register("jira", lambda: JiraExporter())
except (ImportError, ModuleNotFoundError) as e:
    warnings.warn(
        f"Exporter 'jira' unavailable (optional dependency): {e}", RuntimeWarning, stacklevel=2
    )
