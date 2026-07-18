# SPDX-License-Identifier: MPL-2.0
"""Typed input boundary for action-ID deduplication."""

from __future__ import annotations

import pytest

from intrinsical_policy_engine.domain.services.dedup_service import dedupe_ids


def test_dedupe_ids_rejects_untyped_mapping_shape() -> None:
    identifiers = ["CONTROL-A", "ALIAS-A", "CONTROL-B"]
    untyped_dedups = {
        "mappings": [
            {"alias": "ALIAS-A", "canonical": "CONTROL-A"},
        ]
    }

    with pytest.raises(TypeError, match="DedupsContract"):
        dedupe_ids(identifiers, untyped_dedups)  # type: ignore[arg-type]
