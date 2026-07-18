# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""PEP 440 compatibility contracts for public framework-pack resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from intrinsical_policy_engine.api import (
    Engine,
    EngineConfig,
    GateStatus,
    PackCompatibilityError,
    PackCompatibilityMetadataError,
    PackDescriptor,
    PackLicenseMetadataError,
    PackValidationRequest,
)
from intrinsical_policy_engine.api.packs import (
    validate_pack_compatibility,
    validate_pack_license_metadata,
)

STARTER = Path("frameworks/starter").resolve()


def _descriptor(*ranges: str, engine_version: str = "3.0.0a1") -> PackDescriptor:
    return PackDescriptor(
        id="consumer-pack",
        version="3.0.0",
        root=STARTER,
        compatible_engine_versions=tuple(ranges),
        engine_version=engine_version,
    )


def test_alpha_engine_does_not_satisfy_stable_3_0_floor() -> None:
    descriptor = _descriptor(">=3.0.0,<4.0.0")

    with pytest.raises(PackCompatibilityError) as exc_info:
        validate_pack_compatibility(descriptor, engine_version="3.0.0a1")

    assert exc_info.value.engine_version == "3.0.0a1"
    assert exc_info.value.compatible_engine_versions == (">=3.0.0,<4.0.0",)


def test_consumer_can_explicitly_accept_the_engine_alpha() -> None:
    validate_pack_compatibility(
        _descriptor(">=3.0.0a1,<4.0.0"),
        engine_version="3.0.0a1",
    )


def test_declared_ranges_are_alternatives() -> None:
    validate_pack_compatibility(
        _descriptor(">=2.0,<2.1", ">=3.0.0a1,<4.0.0"),
        engine_version="3.0.0a1",
    )


@pytest.mark.parametrize("ranges", [(), ("",), ("   ",), ("not a specifier",)])
def test_missing_or_invalid_compatibility_metadata_is_typed(ranges: tuple[str, ...]) -> None:
    descriptor = _descriptor(*ranges)

    with pytest.raises(PackCompatibilityMetadataError):
        validate_pack_compatibility(descriptor, engine_version="3.0.0a1")


def test_validate_pack_returns_stable_incompatibility_diagnostic() -> None:
    descriptor = _descriptor(">=3.0.0,<4.0.0")

    class IncompatibleProvider:
        def resolve(self, reference: str | Path) -> PackDescriptor:
            assert reference == "registry:consumer"
            return descriptor

    result = Engine(EngineConfig(pack_provider=IncompatibleProvider())).validate_pack(
        PackValidationRequest(pack="registry:consumer")
    )

    assert not result.success
    assert result.pack is None
    assert result.gate.status is GateStatus.BLOCKED
    assert result.diagnostics[0].code == "PACK_ENGINE_INCOMPATIBLE"
    assert result.diagnostics[0].source == str(STARTER)


def test_custom_provider_cannot_override_the_installed_engine_version() -> None:
    descriptor = _descriptor(">=3.0.0,<4.0.0", engine_version="3.1.0")

    class MisleadingProvider:
        def resolve(self, reference: str | Path) -> PackDescriptor:
            assert reference == "registry:consumer"
            return descriptor

    result = Engine(EngineConfig(pack_provider=MisleadingProvider())).validate_pack(
        PackValidationRequest(pack="registry:consumer")
    )

    assert not result.success
    assert result.diagnostics[0].code == "PACK_ENGINE_INCOMPATIBLE"
    assert "installed engine is 3.0.0a1" in result.diagnostics[0].message


@pytest.mark.parametrize("declared", ["/tmp/LICENSE", "../LICENSE", "missing.txt"])
def test_license_file_must_resolve_to_a_file_inside_the_pack(
    tmp_path: Path,
    declared: str,
) -> None:
    pack_root = tmp_path / "neutral-pack"
    pack_root.mkdir()
    descriptor = PackDescriptor(
        id="neutral-pack",
        version="1.0.0",
        root=pack_root,
        compatible_engine_versions=(">=3.0.0a1,<4.0.0",),
        license_file=declared,
    )

    with pytest.raises(PackLicenseMetadataError):
        validate_pack_license_metadata(descriptor)


def test_validate_pack_reports_invalid_license_metadata(tmp_path: Path) -> None:
    descriptor = PackDescriptor(
        id="neutral-pack",
        version="1.0.0",
        root=tmp_path,
        compatible_engine_versions=(">=3.0.0a1,<4.0.0",),
        license_file="missing.txt",
    )

    class InvalidLicenseProvider:
        def resolve(self, reference: str | Path) -> PackDescriptor:
            assert reference == "registry:neutral"
            return descriptor

    result = Engine(EngineConfig(pack_provider=InvalidLicenseProvider())).validate_pack(
        PackValidationRequest(pack="registry:neutral")
    )

    assert not result.success
    assert result.diagnostics[0].code == "PACK_LICENSE_METADATA_INVALID"
    assert result.diagnostics[0].source == str(tmp_path)
