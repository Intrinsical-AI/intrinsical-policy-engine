# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Bundle profile execution strategy using declarative BundleExporter.

Extracted from FilesystemExporter.export() lines 482-543.
Handles:
- Reconstruction of domain entities (SubjectProfile, EvalContext)
- Execution of declarative bundle profiles via BundleExporter
- Coverage logging for quality gating
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from jinja2 import TemplateError

from src.adapters.export.bundles.bundle_exporter import BundleExporter
from src.adapters.export.filesystem.strategies.base import (
    ArtifactsDelta,
    ExportContext,
    ExportStrategy,
)
from src.adapters.frameworks.backlog_loader import load_backlog_config_from_framework_dir
from src.adapters.logging import StructuredLogger
from src.domain.bundles.context import EvalContext
from src.domain.bundles.models import BundleNode, BundleProfile
from src.domain.core.subject_profile import subject_profile_from_dict
from src.domain.exceptions import ExportError

if TYPE_CHECKING:
    from src.adapters.export.filesystem.filesystem_exporter import FilesystemExporter


class BundleProfileStrategy(ExportStrategy):
    """Execute declarative bundle profiles via BundleExporter.

    This strategy:
    1. Reconstructs domain entities (SubjectProfile, EvalContext) from plan
    2. Exports each bundle profile defined in config
    3. Logs coverage for quality gating
    4. Returns ArtifactsDelta with generated files and EvalContext
    """

    def execute(self, exporter: FilesystemExporter, context: ExportContext) -> ArtifactsDelta:
        """Execute bundle profile export pipeline.

        Args:
            exporter: Parent exporter (for assembler access)
            context: Shared export context (read-only preferred)

        Returns:
            ArtifactsDelta with generated files and EvalContext.

        Raises:
            RuntimeError: If strict mode and bundle export fails
        """
        bundle_profiles = context.config.get("bundle_profiles")
        if not bundle_profiles:
            return ArtifactsDelta.empty()

        generated_files: list[Path] = []
        eval_ctx_result: EvalContext | None = None

        try:
            # Reconstruct domain entities
            eval_ctx = self._build_eval_context(context)
            if eval_ctx is None:
                return ArtifactsDelta.empty()

            eval_ctx_result = eval_ctx

            # Create bundle exporter
            bundle_exporter = BundleExporter(context.assembler)

            skip_nodes = set(context.config.get("skip_nodes") or [])

            # Export each profile (except 'reporting' kind - handled by ReportingStrategy)
            if isinstance(bundle_profiles, dict):
                for pid, profile in bundle_profiles.items():
                    if isinstance(profile, BundleProfile):
                        # Skip reporting profiles - they run AFTER EvidenceStrategy
                        # to have access to computed metrics.
                        if profile.kind == "reporting":
                            continue
                        if skip_nodes:
                            filtered_nodes = self._filter_nodes(profile.nodes, skip_nodes)
                            profile = profile.model_copy(update={"nodes": filtered_nodes})

                        coverage = bundle_exporter.export_profile(
                            profile, eval_ctx, context.out_dir
                        )
                        # Collect generated files for delta
                        generated_files.extend(coverage.generated_files)
                        self._log_coverage(exporter, pid, coverage)

        except (ExportError, TemplateError, OSError, ValueError, RuntimeError) as err:
            self._handle_error(exporter, context, err)
            return ArtifactsDelta.empty()

        return ArtifactsDelta(
            generated_files=tuple(generated_files),
            eval_ctx=eval_ctx_result,
        )

    def _filter_nodes(self, nodes: list[BundleNode], skip_ids: set[str]) -> list[BundleNode]:
        """Return nodes with skip_ids removed (recursively)."""
        filtered: list[BundleNode] = []
        for node in nodes:
            if node.id in skip_ids:
                continue
            if node.kind == "dir" and node.children:
                filtered_children = self._filter_nodes(node.children, skip_ids)
                node = node.model_copy(update={"children": filtered_children})
            filtered.append(node)
        return filtered

    def _build_eval_context(self, context: ExportContext) -> EvalContext | None:
        """Build EvalContext from plan data.

        Returns None if required fields are missing.
        """
        plan = context.plan
        system_profile_data = plan.get("system_profile") or {}

        # Validate required fields
        if "risk_tier" not in system_profile_data or "roles" not in system_profile_data:
            return None

        sys_profile = subject_profile_from_dict(system_profile_data)

        # Build flags dict with strict mode propagation
        plan_flags = {f: True for f in plan.get("flags", [])}
        if context.strict:
            plan_flags["strict"] = True

        # Inject runtime/config prepared outside the domain layer.
        extras = dict(context.ctx)
        extras["_contracts_dir"] = str(context.templates_dir.parent)
        extras["_backlog_config"] = load_backlog_config_from_framework_dir(
            context.templates_dir.parent
        )
        bundle = context.config.get("bundle")
        extras["_bundle"] = bundle
        if bundle is not None:
            extras["runtime"] = bundle.runtime

        return EvalContext(
            plan=plan,
            system_profile=sys_profile,
            flags=plan_flags,
            extras=extras,
        )

    def _log_coverage(self, exporter: FilesystemExporter, profile_id: str, coverage: Any) -> None:
        """Log bundle coverage for quality gating."""
        logger: StructuredLogger | None = getattr(exporter, "_logger", None)
        if logger:
            logger.info(
                "bundle.coverage",
                {
                    "profile": profile_id,
                    "actions": len(coverage.covered_actions),
                    "evidences": len(coverage.covered_evidences),
                },
            )

    def _handle_error(
        self, exporter: FilesystemExporter, context: ExportContext, error: Exception
    ) -> None:
        """Handle bundle export errors."""
        logger: StructuredLogger | None = getattr(exporter, "_logger", None)
        if logger:
            logger.error("export.bundle.failed", {"error": str(error)})

        if context.strict:
            raise RuntimeError(f"Failed to export declarative bundles: {error}") from error
