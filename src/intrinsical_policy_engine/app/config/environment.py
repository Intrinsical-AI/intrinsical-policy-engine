# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Typed environment input for the public CLI composition root.

Environment variables are process inputs, not domain configuration.  This
module is the only public-engine location that translates supported ``IPE_*``
names into explicit values passed to application services and adapters.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

LEGACY_ENV_REPLACEMENTS: dict[str, str] = {
    "LEXOPS_ENV": "IPE_ENV",
    "LEXOPS_STRICT_CONTRACTS": "IPE_STRICT_CONTRACTS",
    "LEXOPS_TOLERATE_QUESTIONS_ERRORS": "IPE_TOLERATE_QUESTIONS_ERRORS",
    "LEXOPS_ALLOW_INCOMPLETE_COVERAGE": "IPE_ALLOW_INCOMPLETE_COVERAGE",
    "LEXOPS_DEMO_MODE": "IPE_DEMO_MODE",
    "LEXOPS_DEV_MODE": "IPE_DEV_MODE",
    "LEXOPS_OUT_DIR": "IPE_OUT_DIR",
    "LEXOPS_SKIP_GPG_SIGNING": "IPE_SKIP_GPG_SIGNING",
}

_TRUE_VALUES = frozenset({"1", "true", "yes", "on"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


class EnvironmentConfigurationError(ValueError):
    """Raised when process environment input cannot be mapped safely."""


@dataclass(frozen=True)
class CliEnvironment:
    """Validated environment settings consumed by the ``ipe`` CLI."""

    profile: str = "prod"
    strict_contracts: bool = True
    tolerate_questions_errors: bool = False
    allow_incomplete_coverage: bool | None = None
    demo_mode: bool = False
    dev_mode: bool = False
    out_dir: Path | None = None
    skip_gpg_signing: bool = False

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> CliEnvironment:
        """Validate and translate environment variables without mutating them."""
        legacy = [name for name in LEGACY_ENV_REPLACEMENTS if name in values]
        if legacy:
            migrations = ", ".join(
                f"{name}->{LEGACY_ENV_REPLACEMENTS[name]}" for name in sorted(legacy)
            )
            raise EnvironmentConfigurationError(
                "Legacy product environment variables are not accepted by IPE 3.0; "
                f"migrate or unset: {migrations}"
            )

        profile = values.get("IPE_ENV", "prod").strip().lower() or "prod"
        strict_default = profile != "dev"
        raw_out_dir = values.get("IPE_OUT_DIR", "").strip()

        return cls(
            profile=profile,
            strict_contracts=_read_bool(
                values,
                "IPE_STRICT_CONTRACTS",
                default=strict_default,
            ),
            tolerate_questions_errors=_read_bool(
                values,
                "IPE_TOLERATE_QUESTIONS_ERRORS",
                default=False,
            ),
            allow_incomplete_coverage=_read_optional_bool(
                values,
                "IPE_ALLOW_INCOMPLETE_COVERAGE",
            ),
            demo_mode=_read_bool(values, "IPE_DEMO_MODE", default=False),
            dev_mode=_read_bool(values, "IPE_DEV_MODE", default=False),
            out_dir=Path(raw_out_dir) if raw_out_dir else None,
            skip_gpg_signing=_read_bool(
                values,
                "IPE_SKIP_GPG_SIGNING",
                default=False,
            ),
        )


@dataclass(frozen=True)
class UiEnvironment:
    """Validated process configuration for the optional questionnaire UI."""

    csrf_secret: str
    api_key: str = ""
    visibility_incremental: bool = True
    visibility_strict: bool = True
    force_https: bool = False
    debug: bool = False
    log_level: str = "INFO"

    @classmethod
    def from_mapping(cls, values: Mapping[str, str]) -> UiEnvironment:
        """Translate ``UI_*`` settings at a UI composition boundary."""
        csrf_secret = values.get("UI_CSRF_SECRET", "").strip()
        if not csrf_secret:
            raise EnvironmentConfigurationError(
                "UI_CSRF_SECRET is required to start the questionnaire UI"
            )
        return cls(
            csrf_secret=csrf_secret,
            api_key=values.get("UI_API_KEY", ""),
            visibility_incremental=_read_bool(
                values,
                "UI_VIS_INCREMENTAL",
                default=True,
            ),
            visibility_strict=_read_bool(values, "UI_VIS_STRICT", default=True),
            force_https=_read_bool(values, "UI_FORCE_HTTPS", default=False),
            debug=_read_bool(values, "UI_DEBUG", default=False),
            log_level=values.get("UI_LOG_LEVEL", "INFO").strip().upper() or "INFO",
        )


def load_cli_environment() -> CliEnvironment:
    """Read the current process environment at the CLI boundary."""
    return CliEnvironment.from_mapping(os.environ)


def load_ui_environment() -> UiEnvironment:
    """Read optional UI server settings at its composition boundary."""
    return UiEnvironment.from_mapping(os.environ)


def _read_bool(values: Mapping[str, str], name: str, *, default: bool) -> bool:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUE_VALUES:
        return True
    if normalized in _FALSE_VALUES:
        return False
    allowed = ", ".join(sorted(_TRUE_VALUES | _FALSE_VALUES))
    raise EnvironmentConfigurationError(
        f"Invalid boolean value for {name}: {raw!r}; expected one of {allowed}"
    )


def _read_optional_bool(values: Mapping[str, str], name: str) -> bool | None:
    raw = values.get(name)
    if raw is None or not raw.strip():
        return None
    return _read_bool(values, name, default=False)
