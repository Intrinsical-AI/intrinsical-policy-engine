# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Structured immutable conditions shared by every pack contract."""

from __future__ import annotations

from collections.abc import Set
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, RootModel, model_validator

Identifier = Annotated[str, Field(min_length=1, pattern=r"^\S+$")]


class _Node(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True, strict=True, serialize_by_alias=True)


class Has(_Node):
    has: Identifier


class AnyPrefix(_Node):
    any_prefix: Identifier


class All(_Node):
    all: tuple[Condition, ...] = Field(strict=False)


class AnyOf(_Node):
    any: tuple[Condition, ...] = Field(strict=False)


class Not(_Node):
    not_: Condition = Field(alias="not")


class Condition(RootModel[Has | AnyPrefix | All | AnyOf | Not]):
    model_config = ConfigDict(frozen=True)

    @model_validator(mode="before")
    @classmethod
    def check_shape(cls, value: object) -> object:
        if isinstance(value, (Has, AnyPrefix, All, AnyOf, Not)):
            return value
        if not isinstance(value, dict) or len(value) != 1:
            raise ValueError("condition must be a mapping with exactly one operator")
        operator = next(iter(value))
        if operator not in {"has", "any_prefix", "all", "any", "not"}:
            raise ValueError(f"unknown condition operator: {operator}")
        return value

    @classmethod
    def true(cls) -> Condition:
        return cls(root=All(all=()))

    def children(self) -> tuple[Condition, ...]:
        node = self.root
        if isinstance(node, All):
            return node.all
        if isinstance(node, AnyOf):
            return node.any
        if isinstance(node, Not):
            return (node.not_,)
        return ()

    def positive(self) -> bool:
        return not isinstance(self.root, Not) and all(c.positive() for c in self.children())


All.model_rebuild()
AnyOf.model_rebuild()
Not.model_rebuild()
Condition.model_rebuild()


def evaluate_condition(condition: Condition, flags: Set[str]) -> bool:
    node = condition.root
    if isinstance(node, Has):
        return node.has in flags
    if isinstance(node, AnyPrefix):
        return any(namespace_matches(flag, node.any_prefix) for flag in flags)
    if isinstance(node, All):
        return all(evaluate_condition(child, flags) for child in node.all)
    if isinstance(node, AnyOf):
        return any(evaluate_condition(child, flags) for child in node.any)
    return not evaluate_condition(node.not_, flags)


def condition_references(condition: Condition) -> tuple[set[str], set[str]]:
    flags: set[str] = set()
    prefixes: set[str] = set()
    node = condition.root
    if isinstance(node, Has):
        flags.add(node.has)
    elif isinstance(node, AnyPrefix):
        prefixes.add(node.any_prefix)
    for child in condition.children():
        child_flags, child_prefixes = condition_references(child)
        flags.update(child_flags)
        prefixes.update(child_prefixes)
    return flags, prefixes


def namespace_matches(flag: str, prefix: str) -> bool:
    return flag == prefix or flag.startswith(prefix + ".")
