# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Typed containers describing exporter inputs/outputs."""

from __future__ import annotations

from dataclasses import dataclass


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
    content_hash: str
    version: str = "v1"
