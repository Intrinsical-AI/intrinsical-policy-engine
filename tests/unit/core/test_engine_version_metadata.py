# SPDX-License-Identifier: MPL-2.0
"""All version metadata helpers must use the canonical engine identity."""

from __future__ import annotations

from collections.abc import Callable

from intrinsical_policy_engine.api.packs import installed_engine_version
from intrinsical_policy_engine.app.rendering import artifact_renderer
from intrinsical_policy_engine.common.constants import (
    CANONICAL_ENGINE_NAME,
    CANONICAL_ENGINE_VERSION,
)
from intrinsical_policy_engine.domain.bundles import context_builders
from intrinsical_policy_engine.domain.services import assess_service


def _version_helpers() -> tuple[Callable[[], str], ...]:
    return (
        assess_service._get_engine_version,
        context_builders._get_engine_version,
        artifact_renderer._get_engine_version,
    )


def test_version_helpers_use_canonical_constant() -> None:
    assert all(helper() == CANONICAL_ENGINE_VERSION for helper in _version_helpers())


def test_canonical_distribution_identity_is_stable() -> None:
    assert CANONICAL_ENGINE_NAME == "intrinsical-policy-engine"
    assert CANONICAL_ENGINE_VERSION.startswith("3.0.")
    assert installed_engine_version() == CANONICAL_ENGINE_VERSION
