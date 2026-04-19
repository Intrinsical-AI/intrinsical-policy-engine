# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Regulatory metadata provider (Domain Service).

Owns the loading and construction of regulatory interpretation metadata.
This is a pure domain service with no filesystem dependencies.

Per docs/invariants/ENGINE-ARCHITECTURE-v1.md: Domain services transform data in memory,
they don't do I/O. The contract adapter handles file loading.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RegulatoryMeta:
    """Regulatory interpretation metadata from rules.yml.

    Attributes:
        version: Semantic version of the regulatory interpretation
        effective_date: Date this interpretation became effective
        source: Authoritative source for the active framework
        supersedes: List of previous versions this supersedes
        rationale: Explanation of interpretation basis
    """

    version: str
    effective_date: str
    source: str
    supersedes: tuple[str, ...] = ()
    rationale: str | None = None


@dataclass(frozen=True)
class RegulatoryWarnings:
    """Warnings about regulatory interpretation limitations.

    These warnings inform users about caveats in the assessment,
    such as provisional standards, pending guidance, etc.
    """

    warnings: tuple[str, ...]

    def __bool__(self) -> bool:
        return len(self.warnings) > 0


def extract_regulatory_meta(rules_data: dict[str, Any]) -> RegulatoryMeta | None:
    """Extract regulatory metadata from parsed rules.yml.

    Parses the 'regulatory_meta' section from rules.yml and constructs a
    RegulatoryMeta object. Returns None if the section is missing or invalid.

    Args:
        rules_data: Parsed YAML content from rules.yml (dict).

    Returns:
        RegulatoryMeta object if valid regulatory_meta block found, None otherwise.

    Example:
        >>> rules = {"regulatory_meta": {"version": "1.0.0", "source": "Framework source"}}
        >>> meta = extract_regulatory_meta(rules)
        >>> assert meta.version == "1.0.0"
    """
    if not isinstance(rules_data, dict):
        return None

    reg_meta = rules_data.get("regulatory_meta")
    if not isinstance(reg_meta, dict):
        return None

    version = reg_meta.get("version")
    if not version:
        return None

    supersedes_raw = reg_meta.get("supersedes") or []
    supersedes = tuple(str(s) for s in supersedes_raw) if isinstance(supersedes_raw, list) else ()

    return RegulatoryMeta(
        version=str(version),
        effective_date=str(reg_meta.get("effective_date", "N/A")),
        source=str(reg_meta.get("source", "N/A")),
        supersedes=supersedes,
        rationale=(
            str(reg_meta.get("rationale")) if reg_meta.get("rationale") is not None else None
        ),
    )


def build_regulatory_warnings(
    reg_meta: RegulatoryMeta,
    routing_route: str | None,
) -> RegulatoryWarnings:
    """Build regulatory warnings from metadata and plan context.

    Generates contextual warnings about framework interpretation limitations and
    general interpretation disclaimers.

    Args:
        reg_meta: Parsed regulatory metadata containing version and source info.
        routing_route: Current routing assessment route. Used to generate
            route-specific warnings.

    Returns:
        RegulatoryWarnings object containing applicable warning messages.

    Example:
        >>> meta = RegulatoryMeta(version="1.0.0", effective_date="2024-08-01", source="source")
        >>> warnings = build_regulatory_warnings(meta, "enhanced-review")
        >>> assert len(warnings.warnings) > 0
    """
    warnings: list[str] = []

    if routing_route == "enhanced-review":
        warnings.append(
            f"Enhanced review route selected as of {reg_meta.effective_date}; "
            "confirm route assumptions before operational reliance."
        )

    # General interpretation disclaimer
    warnings.append(
        f"This assessment is based on framework interpretation v{reg_meta.version} "
        f"({reg_meta.source}). Later framework updates may alter classification."
    )

    return RegulatoryWarnings(warnings=tuple(warnings))


def regulatory_meta_to_dict(meta: RegulatoryMeta) -> dict[str, Any]:
    """Convert RegulatoryMeta to dict for template context.

    Args:
        meta: RegulatoryMeta object to convert.

    Returns:
        Dictionary representation suitable for Jinja2 template rendering.
    """
    return {
        "version": meta.version,
        "effective_date": meta.effective_date,
        "source": meta.source,
        "supersedes": list(meta.supersedes),
        "rationale": meta.rationale,
    }
