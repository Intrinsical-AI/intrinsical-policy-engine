# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Backlog configuration loading for framework packs."""

from __future__ import annotations

from pathlib import Path

import yaml

from intrinsical_policy_engine.adapters.frameworks.layout_loader import load_framework_layout
from intrinsical_policy_engine.domain.bundles.backlog_config import build_backlog_config
from intrinsical_policy_engine.domain.bundles.models import BacklogConfig


def load_backlog_config_from_framework_dir(contracts_dir: Path) -> BacklogConfig:
    """Load backlog_config.yml from a framework pack.

    Pack resolution errors (missing manifest, invalid layout) and YAML/schema
    errors surface as exceptions so regressions are observable instead of
    silently degrading to defaults.
    """
    layout = load_framework_layout(contracts_dir)
    data = yaml.safe_load(layout.backlog_config_path.read_text(encoding="utf-8"))
    return build_backlog_config(data)
