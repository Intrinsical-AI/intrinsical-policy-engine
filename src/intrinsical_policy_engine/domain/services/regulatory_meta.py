# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Interpretation notices from already validated pack metadata."""

from dataclasses import dataclass

from intrinsical_policy_engine.domain.contract_models import RegulatoryMeta


@dataclass(frozen=True)
class RegulatoryWarnings:
    warnings: tuple[str, ...]


def build_regulatory_warnings(meta: RegulatoryMeta) -> RegulatoryWarnings:
    return RegulatoryWarnings(
        warnings=(
            f"This assessment is based on framework interpretation v{meta.version} "
            f"({meta.source}). Later framework updates may alter classification.",
        )
    )
