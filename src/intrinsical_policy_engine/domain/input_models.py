# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Validated assessment input, shared by the CLI and library boundary."""

from __future__ import annotations

import math
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator


def _require_finite_numbers(value: JsonValue) -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("metadata numbers must be finite")
    if isinstance(value, dict):
        for item in value.values():
            _require_finite_numbers(item)
    elif isinstance(value, list):
        for item in value:
            _require_finite_numbers(item)


class InputMetadata(BaseModel):
    """User supplied metadata; values must be JSON data, never Python objects."""

    model_config = ConfigDict(strict=True, extra="allow", allow_inf_nan=False)
    __pydantic_extra__: dict[str, JsonValue] = Field(init=False)

    @model_validator(mode="after")
    def finite_metadata(self) -> InputMetadata:
        # JsonValue's recursive type does not inherit the model's numeric config.
        for value in (self.__pydantic_extra__ or {}).values():
            _require_finite_numbers(value)
        return self


class SystemMetadata(InputMetadata):
    name: Annotated[str, Field(min_length=1)] | None = None

    @model_validator(mode="after")
    def valid_name(self) -> SystemMetadata:
        if self.name is not None and not self.name.strip():
            raise ValueError("system.name must be a nonempty string")
        return self


class AssessmentInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)

    answers: dict[str, str | list[str] | None] | None = None
    flags: list[str] | None = None
    system: SystemMetadata = Field(default_factory=SystemMetadata)
    provider: InputMetadata = Field(default_factory=InputMetadata)
    owner: InputMetadata = Field(default_factory=InputMetadata)
    declared_by: InputMetadata = Field(default_factory=InputMetadata)
    approvals: InputMetadata = Field(default_factory=InputMetadata)
    meta: InputMetadata = Field(default_factory=InputMetadata)

    @model_validator(mode="before")
    @classmethod
    def one_input(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            raise ValueError("assessment input must be a mapping")
        if ("answers" in value) == ("flags" in value):
            raise ValueError("provide exactly one of answers or flags")
        if value.get("answers" if "answers" in value else "flags") is None:
            raise ValueError("answers/flags cannot be null")
        return value

    @model_validator(mode="after")
    def unique_flags(self) -> AssessmentInput:
        if self.flags is not None:
            if any(not flag or flag.strip() != flag for flag in self.flags):
                raise ValueError(
                    "flags must be nonempty identifiers without surrounding whitespace"
                )
            if len(set(self.flags)) != len(self.flags):
                raise ValueError("flags cannot contain duplicates")
        return self
