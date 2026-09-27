# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Domain models for declarative bundle profiles (Domain 3).

These models represent the structure defined in `bundle_profiles.yml`
and `backlog_config.yml`.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# =============================================================================
# BACKLOG CONFIGURATION (migrated from hardcoded ENGINEERING_KEYWORDS)
# =============================================================================


class BacklogSplitRule(BaseModel):
    """Rule for splitting actions into categorized backlogs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., description="Unique identifier for this split (e.g., 'engineering')")
    filename: str = Field(..., description="Output filename (e.g., 'engineering_backlog.csv')")
    keywords: list[str] = Field(
        default_factory=list,
        description="Keywords to match in action id/title (case-insensitive)",
    )
    description: str = Field(default="", description="Human-readable description")


class BacklogConfig(BaseModel):
    """Declarative configuration for backlog generation.

    Replaces hardcoded ENGINEERING_KEYWORDS in LegacyBacklogStrategy.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: str = Field(default="1.0.0", description="Config version")
    default_due_days: int = Field(
        default=30, description="Days from generation for default due date"
    )
    splits: list[BacklogSplitRule] = Field(
        default_factory=list,
        description="Rules for splitting actions into separate backlogs",
    )
    fallback_split_id: str = Field(
        default="governance",
        description="Split ID for actions not matching any keyword rule",
    )


# =============================================================================
# BUNDLE PROFILES
# =============================================================================


class BundleNode(BaseModel):
    """A node in the bundle directory tree (file, dir, or copy)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., description="Stable identifier for the node")
    kind: Literal["file", "dir", "copy"]
    name: str | None = Field(None, description="Filename or directory name (required for file/dir)")

    # For dirs
    children: list[BundleNode] = Field(default_factory=list)

    # For files
    template: str | None = Field(
        None, description="Path to Jinja2 template (relative to templates root)"
    )
    context: str = Field("default", description="Context builder profile to use")

    # For copies
    source: str | None = Field(
        None, description="Source path for copy (relative to project root or output)"
    )
    target: str | None = Field(None, description="Target path relative to bundle root")

    # Logic
    predicates: list[str] = Field(
        default_factory=list, description="List of predicates that must be true"
    )

    # Coverage metrics (INV-B1)
    # If False, this node's trace_back_to doesn't count toward coverage metrics.
    # Use for technical artifacts (backlog.csv, summary.json) that use wildcards.
    # Defaults to True: evidences count by default, opt-out for technical.
    counts_for_coverage: bool = Field(
        default=True,
        description="If False, node is excluded from INV-B1 coverage metrics",
    )

    # Traceability
    trace_back_to: dict[str, list[str] | bool] | None = Field(
        None, description="Traceability metadata (actions, evidences)"
    )


class BundleProfile(BaseModel):
    """A profile defining a complete output bundle."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    id: str = Field(..., description="Unique identifier for the profile")
    kind: str = Field(..., description="Type of bundle (provider, deployer, etc.)")
    root_dir: str = Field(..., description="Root directory name for the bundle")
    applies_if: list[str] = Field(
        default_factory=list, description="Predicates determining if this profile applies"
    )
    nodes: list[BundleNode] = Field(default_factory=list)
