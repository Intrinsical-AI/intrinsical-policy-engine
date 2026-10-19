# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Shared label mapping and offline request envelopes."""

from __future__ import annotations

from typing import Any


def map_labels_to_ids(
    labels: list[str], label_map: dict[str, str], extra_ids: list[str]
) -> list[str]:
    mapped = [label_map[label] for label in labels if label in label_map]
    return list(dict.fromkeys([*mapped, *extra_ids]))


def build_common_record(
    *, method: str, url: str, base_url: str, headers: dict[str, str], body: Any, uid: str
) -> dict[str, Any]:
    return {
        "method": method,
        "url": url,
        "base_url": base_url,
        "headers": headers,
        "body": body,
        "uid": uid,
    }
