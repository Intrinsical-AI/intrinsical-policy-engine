# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Canonical assessment artifacts; errors propagate to abort publication."""

import json
from copy import deepcopy
from pathlib import Path
from typing import Any

from intrinsical_policy_engine.common.document_io import validate_document_graph
from intrinsical_policy_engine.domain.snapshot_contract import (
    MAX_SNAPSHOT_METADATA_DEPTH,
    MAX_SNAPSHOT_METADATA_NODES,
    SNAPSHOT_FORMAT_VERSION,
    validate_snapshot_metadata,
)


def _validate_metadata_graph(data: Any) -> None:
    validate_document_graph(
        data, max_depth=MAX_SNAPSHOT_METADATA_DEPTH, max_nodes=MAX_SNAPSHOT_METADATA_NODES
    )


def _json_content(data: Any) -> str:
    _validate_metadata_graph(data)
    return json.dumps(data, indent=2, ensure_ascii=False, sort_keys=True, allow_nan=False) + "\n"


def _write_json_content(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)


def write_json(path: Path, data: Any) -> None:
    _write_json_content(path, _json_content(data))


def write_assessment(plan: dict[str, Any], outdir: Path) -> None:
    # Validate before deepcopy, whose recursion is unsafe for arbitrary caller
    # graphs; then check the versioned documents before writing either file.
    _validate_metadata_graph(plan)
    summary = deepcopy(plan)
    trace = summary.get("trace")
    if not isinstance(trace, dict):
        raise ValueError("Assessment trace must be an object")
    summary["snapshot_format_version"] = SNAPSHOT_FORMAT_VERSION
    trace["snapshot_format_version"] = SNAPSHOT_FORMAT_VERSION
    summary_content = _json_content(summary)
    trace_content = _json_content(trace)
    validate_snapshot_metadata(summary, trace)
    _write_json_content(outdir / "_metadata" / "summary.json", summary_content)
    _write_json_content(outdir / "_metadata" / "trace.json", trace_content)
