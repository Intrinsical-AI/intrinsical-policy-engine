# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""The four supported offline exporters."""

from collections.abc import Callable
from typing import TypeAlias

from intrinsical_policy_engine.adapters.export.asana.asana_exporter import AsanaExporter
from intrinsical_policy_engine.adapters.export.filesystem.filesystem_exporter import (
    FilesystemExporter,
)
from intrinsical_policy_engine.adapters.export.jira.jira_exporter import JiraExporter
from intrinsical_policy_engine.adapters.export.linear.linear_exporter import LinearExporter
from intrinsical_policy_engine.domain.exceptions import ExporterNotFoundError

OfflineExporter: TypeAlias = FilesystemExporter | AsanaExporter | JiraExporter | LinearExporter


def get_exporter(name: str) -> OfflineExporter:
    factories: dict[str, Callable[[], OfflineExporter]] = {
        "filesystem": FilesystemExporter,
        "asana": AsanaExporter,
        "jira": JiraExporter,
        "linear": LinearExporter,
    }
    if name not in factories:
        raise ExporterNotFoundError(name, sorted(factories))
    return factories[name]()
