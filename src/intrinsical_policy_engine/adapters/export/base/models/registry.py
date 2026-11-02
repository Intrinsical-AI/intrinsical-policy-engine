# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""The three offline API exporters; filesystem export is built by the orchestrator."""

from typing import Any

from intrinsical_policy_engine.adapters.export.asana.asana_exporter import AsanaExporter
from intrinsical_policy_engine.adapters.export.base.exporters.base_api_exporter import (
    BaseApiExporter,
)
from intrinsical_policy_engine.adapters.export.jira.jira_exporter import JiraExporter
from intrinsical_policy_engine.adapters.export.linear.linear_exporter import LinearExporter

API_EXPORTERS: dict[str, type[BaseApiExporter[Any]]] = {
    exporter.target: exporter for exporter in (AsanaExporter, JiraExporter, LinearExporter)
}
