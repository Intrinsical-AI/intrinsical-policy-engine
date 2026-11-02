# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Strict configuration for offline export payload generation."""

from __future__ import annotations

import json
import re
import unicodedata
from ipaddress import IPv6Address
from typing import Annotated
from urllib.parse import urlsplit

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, JsonValue, field_validator


def _without_controls(value: str) -> str:
    if any(unicodedata.category(character) == "Cc" for character in value):
        raise ValueError("Configuration strings must not contain control characters")
    return value


def _identifier(value: str) -> str:
    if not value.strip():
        raise ValueError("Configuration identifiers must not be blank")
    return _without_controls(value)


def _header_name(value: str) -> str:
    if not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", value):
        raise ValueError("HTTP header names must be nonempty ASCII tokens")
    return value


Identifier = Annotated[str, AfterValidator(_identifier)]
HeaderName = Annotated[str, AfterValidator(_header_name)]
HeaderValue = Annotated[str, AfterValidator(_without_controls)]


class ExportConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ApiExportConfig(ExportConfig):
    base_url: str = Field(min_length=1)
    headers: dict[HeaderName, HeaderValue] = Field(default_factory=dict)

    @field_validator("base_url")
    @classmethod
    def http_url(cls, value: str) -> str:
        if any(
            character.isspace() or ord(character) < 32 or ord(character) == 127
            for character in value
        ):
            raise ValueError("URL must not contain whitespace or control characters")
        try:
            url = urlsplit(value)
            host = url.hostname
        except ValueError as error:
            raise ValueError("URL must have a valid HTTP(S) authority") from error
        if (
            url.scheme not in {"http", "https"}
            or not host
            or url.username is not None
            or url.password is not None
        ):
            raise ValueError("URL must be an absolute HTTP(S) URL without credentials")
        # Accessing port also rejects nonnumeric and out-of-range values.
        try:
            port = url.port
        except ValueError as error:
            raise ValueError("URL port must be an integer between 0 and 65535") from error
        if port is None and url.netloc.endswith(":"):
            raise ValueError("URL port cannot be empty")
        try:
            if ":" in host:
                IPv6Address(host)
                ascii_host = None
            else:
                ascii_host = host.encode("idna").decode("ascii").removesuffix(".")
        except ValueError as error:
            raise ValueError("URL must have a valid hostname") from error
        if ascii_host is not None and (
            len(ascii_host) > 253
            or any(
                not re.fullmatch(r"[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?", label)
                for label in ascii_host.split(".")
            )
        ):
            raise ValueError("URL must have a valid hostname")
        return value


class AsanaExportConfig(ApiExportConfig):
    project_gid: Identifier
    tag_gids: list[Identifier] = Field(default_factory=list)
    tag_gids_by_label: dict[Identifier, Identifier] = Field(default_factory=dict)


class JiraExportConfig(ApiExportConfig):
    project_key: Identifier
    issue_type: Identifier = "Task"
    customfields: dict[str, JsonValue] = Field(default_factory=dict)

    @field_validator("customfields")
    @classmethod
    def no_reserved_override(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        json.dumps(value, allow_nan=False)
        reserved = {"project", "issuetype", "summary", "description", "duedate", "labels"}
        if value.keys() & reserved:
            raise ValueError("customfields cannot override generated Jira fields")
        return value


class LinearExportConfig(ApiExportConfig):
    teamId: Identifier
    projectId: Identifier | None = None
    labelIds: list[Identifier] = Field(default_factory=list)
    labelIds_by_label: dict[Identifier, Identifier] = Field(default_factory=dict)


class FilesystemExportConfig(ExportConfig):
    """Filesystem rendering needs no target specific settings."""


class ExportSettings(ExportConfig):
    uid_namespace: Identifier | None = None
    profiles: list[Identifier] | None = None
    filesystem: FilesystemExportConfig = Field(default_factory=FilesystemExportConfig)
    asana: AsanaExportConfig | None = None
    jira: JiraExportConfig | None = None
    linear: LinearExportConfig | None = None

    def for_target(self, target: str) -> ApiExportConfig | FilesystemExportConfig:
        settings: dict[str, ApiExportConfig | FilesystemExportConfig | None] = {
            "filesystem": self.filesystem,
            "asana": self.asana,
            "jira": self.jira,
            "linear": self.linear,
        }
        if target not in settings:
            raise ValueError(f"Unknown export target: {target}")
        selected = settings[target]
        if selected is None:
            raise ValueError(f"Missing configuration for target: {target}")
        return selected
