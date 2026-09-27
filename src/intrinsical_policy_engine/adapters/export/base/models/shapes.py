# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Typed containers describing exporter inputs/outputs."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ExportResult:
    """Summary of a single exporter run."""

    target: str
    outputs: list[str]
    stats: dict[str, Any] = field(default_factory=dict)


@dataclass
class TaskItem:
    """Normalized task record consumed by downstream exporters."""

    uid: str
    action_id: str
    title: str
    description_md: str
    due: str | None
    labels: list[str]
    article_ids: list[str]
    priority: str | None = None
    applies_to: str | None = None
    attachments: list[str] = field(default_factory=list)
    # Stable content identity and version metadata (optional for backwards-compat)
    content_hash: str = ""
    version: str = "v1"
