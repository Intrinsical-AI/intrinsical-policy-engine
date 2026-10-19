# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Pure snapshot format, metadata and path invariants shared by all boundaries."""

import json
from pathlib import PurePosixPath
from typing import Any

SNAPSHOT_FORMAT_VERSION = 1
MAX_SNAPSHOT_METADATA_DEPTH = 128
MAX_SNAPSHOT_METADATA_NODES = 1_000_000
CHECKSUMS_NAME = "CHECKSUMS.sha256"
SIGNATURE_NAME = "CHECKSUMS.sha256.asc"
CONTROL_PATHS = frozenset({CHECKSUMS_NAME, SIGNATURE_NAME})
REQUIRED_METADATA = frozenset({"_metadata/summary.json", "_metadata/trace.json"})


class PackageIntegrityError(ValueError):
    """A snapshot violates its closed inventory or path contract."""


def _versioned_document(value: object, relative: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise PackageIntegrityError(f"Required metadata is not an object: {relative}")
    if "snapshot_format_version" not in value:
        raise PackageIntegrityError(
            f"Missing snapshot_format_version in {relative}; recreate the snapshot with this engine"
        )
    version = value["snapshot_format_version"]
    if type(version) is not int or version != SNAPSHOT_FORMAT_VERSION:
        raise PackageIntegrityError(
            f"Unsupported snapshot_format_version in {relative}; "
            f"expected integer {SNAPSHOT_FORMAT_VERSION}"
        )
    return value


def validate_snapshot_metadata(summary: object, trace: object) -> None:
    """Require the current format and the same JSON trace in both documents."""
    summary_document = _versioned_document(summary, "_metadata/summary.json")
    trace_document = _versioned_document(trace, "_metadata/trace.json")
    embedded = summary_document.get("trace")
    if not isinstance(embedded, dict):
        raise PackageIntegrityError("Assessment summary.trace must be an object")
    try:
        # JSON equality preserves numeric/boolean distinctions that Python's
        # mapping equality loses (True == 1 and 1.0 == 1).
        embedded_json = json.dumps(embedded, sort_keys=True, separators=(",", ":"), allow_nan=False)
        trace_json = json.dumps(
            trace_document, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
    except (TypeError, ValueError) as exc:
        raise PackageIntegrityError("Assessment summary.trace is not valid JSON") from exc
    if embedded_json != trace_json:
        raise PackageIntegrityError("Assessment summary.trace differs from _metadata/trace.json")


def canonical_path(value: str) -> str:
    """Require an unambiguous relative POSIX path representable in checksums."""
    path = PurePosixPath(value)
    if (
        value in {"", "."}
        or path.is_absolute()
        or path.as_posix() != value
        or any(part in {".", ".."} for part in path.parts)
        or any(ord(char) < 32 or ord(char) == 127 or char in "\\:" for char in value)
    ):
        raise PackageIntegrityError(f"Unsafe package path: {value!r}")
    return value
