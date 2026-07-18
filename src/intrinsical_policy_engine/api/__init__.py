# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Supported embedding API for Intrinsical Policy Engine 3.x."""

from intrinsical_policy_engine.api.engine import Engine, EngineConfig
from intrinsical_policy_engine.api.errors import (
    PackCompatibilityError,
    PackCompatibilityMetadataError,
    PackError,
    PackLicenseMetadataError,
    PackMetadataError,
)
from intrinsical_policy_engine.api.models import (
    AssessmentRequest,
    AssessmentResult,
    Diagnostic,
    DiagnosticSeverity,
    ExecutionPolicy,
    ExportRequest,
    ExportResult,
    GateCheck,
    GateDecision,
    GateReport,
    GateStatus,
    PackDescriptor,
    PackValidationRequest,
    PackValidationResult,
    ProductIdentity,
    SealRequest,
    SealResult,
    evaluate_gate,
)
from intrinsical_policy_engine.api.packs import PackProvider

__all__ = [
    "AssessmentRequest",
    "AssessmentResult",
    "Diagnostic",
    "DiagnosticSeverity",
    "Engine",
    "EngineConfig",
    "ExecutionPolicy",
    "ExportRequest",
    "ExportResult",
    "GateCheck",
    "GateDecision",
    "GateReport",
    "GateStatus",
    "PackCompatibilityError",
    "PackCompatibilityMetadataError",
    "PackDescriptor",
    "PackError",
    "PackLicenseMetadataError",
    "PackMetadataError",
    "PackProvider",
    "PackValidationRequest",
    "PackValidationResult",
    "ProductIdentity",
    "SealRequest",
    "SealResult",
    "evaluate_gate",
]
