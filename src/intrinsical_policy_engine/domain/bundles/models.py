# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Strict declarative output profiles and backlog configuration."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from intrinsical_policy_engine.domain.conditions import Condition, Identifier
from intrinsical_policy_engine.domain.contract_models import ContractModel, Strings


class BacklogSplitRule(ContractModel):
    id: Identifier
    filename: Identifier
    keywords: tuple[str, ...] = Field(default=(), strict=False)
    description: str = ""


class BacklogConfig(ContractModel):
    splits: tuple[BacklogSplitRule, ...] = Field(default=(), strict=False)
    fallback_split_id: Identifier

    @model_validator(mode="after")
    def valid_splits(self) -> BacklogConfig:
        ids = [split.id for split in self.splits]
        if len(ids) != len(set(ids)):
            raise ValueError("backlog split ids must be unique")
        filenames = [split.filename for split in self.splits]
        if len(filenames) != len(set(filenames)):
            raise ValueError("backlog split filenames must be unique")
        if self.fallback_split_id not in ids:
            raise ValueError("fallback split id must identify a configured split")
        return self


class TraceBackTo(ContractModel):
    actions: Strings = ()
    evidences: Strings = ()


class BundleNode(ContractModel):
    id: Identifier
    kind: Literal["file", "dir", "copy"]
    name: str | None = None
    children: tuple[BundleNode, ...] = Field(default=(), strict=False)
    template: str | None = None
    context: Literal[
        "default", "audit_trail", "backlog", "engineering_backlog", "legal_backlog", "calendar"
    ] = "default"
    source: str | None = None
    target: str | None = None
    when: Condition = Field(default_factory=Condition.true)
    counts_for_coverage: bool = True
    trace_back_to: TraceBackTo = Field(default_factory=TraceBackTo)

    @model_validator(mode="after")
    def node_shape(self) -> BundleNode:
        if self.kind == "file":
            if not self.name or not self.template or self.children or self.source or self.target:
                raise ValueError(
                    "file nodes need name/template and cannot have children/source/target"
                )
        elif self.kind == "dir":
            if not self.name or self.template or self.source or self.target:
                raise ValueError("dir nodes need name and cannot have template/source/target")
        elif not self.source or not self.target or self.name or self.template or self.children:
            raise ValueError("copy nodes need source/target and no name/template/children")
        return self


class BundleProfile(ContractModel):
    id: Identifier
    kind: Identifier
    root_dir: Identifier
    when: Condition = Field(default_factory=Condition.true)
    nodes: tuple[BundleNode, ...] = Field(default=(), strict=False)


class BundleProfilesContract(ContractModel):
    profiles: dict[Identifier, BundleProfile]

    @model_validator(mode="after")
    def matching_keys(self) -> BundleProfilesContract:
        if any(key != profile.id for key, profile in self.profiles.items()):
            raise ValueError("profile mapping keys must match each profile id")
        return self
