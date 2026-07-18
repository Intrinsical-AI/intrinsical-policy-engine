# SPDX-License-Identifier: MPL-2.0
"""Contracts for the 3.0 process-environment boundary."""

from __future__ import annotations

from pathlib import Path

import pytest

from intrinsical_policy_engine.app.cli.main import main
from intrinsical_policy_engine.app.config.environment import (
    LEGACY_ENV_REPLACEMENTS,
    CliEnvironment,
    EnvironmentConfigurationError,
    UiEnvironment,
)


def test_cli_environment_has_safe_production_defaults() -> None:
    environment = CliEnvironment.from_mapping({})

    assert environment.profile == "prod"
    assert environment.strict_contracts is True
    assert environment.allow_incomplete_coverage is None
    assert environment.skip_gpg_signing is False
    assert environment.out_dir is None


def test_cli_environment_translates_supported_inputs() -> None:
    environment = CliEnvironment.from_mapping(
        {
            "IPE_ENV": "dev",
            "IPE_STRICT_CONTRACTS": "yes",
            "IPE_TOLERATE_QUESTIONS_ERRORS": "1",
            "IPE_ALLOW_INCOMPLETE_COVERAGE": "true",
            "IPE_DEMO_MODE": "on",
            "IPE_DEV_MODE": "yes",
            "IPE_OUT_DIR": "build/output",
            "IPE_SKIP_GPG_SIGNING": "1",
        }
    )

    assert environment.profile == "dev"
    assert environment.strict_contracts is True
    assert environment.tolerate_questions_errors is True
    assert environment.allow_incomplete_coverage is True
    assert environment.demo_mode is True
    assert environment.dev_mode is True
    assert environment.out_dir == Path("build/output")
    assert environment.skip_gpg_signing is True


def test_dev_profile_defaults_to_tolerant_contracts() -> None:
    assert CliEnvironment.from_mapping({"IPE_ENV": "dev"}).strict_contracts is False


@pytest.mark.parametrize("legacy,replacement", LEGACY_ENV_REPLACEMENTS.items())
def test_legacy_product_environment_is_rejected(legacy: str, replacement: str) -> None:
    with pytest.raises(EnvironmentConfigurationError) as exc_info:
        CliEnvironment.from_mapping({legacy: "1"})

    message = str(exc_info.value)
    assert legacy in message
    assert replacement in message


def test_invalid_boolean_is_not_silently_coerced() -> None:
    with pytest.raises(EnvironmentConfigurationError, match="IPE_DEV_MODE"):
        CliEnvironment.from_mapping({"IPE_DEV_MODE": "sometimes"})


def test_public_cli_rejects_legacy_environment_before_dispatch(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    monkeypatch.setenv("LEXOPS_ENV", "dev")

    assert main() == 2
    assert "LEXOPS_ENV->IPE_ENV" in capsys.readouterr().err


def test_ui_environment_requires_security_secret() -> None:
    with pytest.raises(EnvironmentConfigurationError, match="UI_CSRF_SECRET"):
        UiEnvironment.from_mapping({})


def test_only_configuration_boundary_reads_process_environment() -> None:
    package_root = Path(__file__).parents[2] / "src" / "intrinsical_policy_engine"
    boundary = package_root / "app" / "config" / "environment.py"

    offenders: list[str] = []
    for source in package_root.rglob("*.py"):
        if source == boundary:
            continue
        text = source.read_text(encoding="utf-8")
        if "os.getenv" in text or "os.environ" in text:
            offenders.append(str(source.relative_to(package_root)))

    assert offenders == []
