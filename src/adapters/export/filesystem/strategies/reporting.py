# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Reporting strategy: Renders summaries and dashboards using metrics.

This strategy runs AFTER evidence collection, consuming metrics to populate
reporting templates (e.g. plan_backlog.md, dashboards).

Reference: docs/invariants/ENGINE-ARCHITECTURE-v1.md (BundleBlueprint & BundleProfiles)
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from src.adapters.export.bundles.bundle_exporter import BundleExporter
from src.adapters.export.filesystem.strategies.base import (
    ArtifactsDelta,
    ExportContext,
    ExportStrategy,
)
from src.adapters.logging import StructuredLogger
from src.domain.bundles.context import EvalContext
from src.domain.bundles.models import BundleProfile

if TYPE_CHECKING:
    from src.adapters.export.filesystem.filesystem_exporter import FilesystemExporter


class ReportingStrategy(ExportStrategy):
    """Execute 'reporting' bundle profiles.

    These profiles differ from standard bundles because they have access
    to the full artifact context including evidence metrics (coverage, quality).

    This strategy runs LAST in the content generation phase (before Manifest)
    to ensure all metrics are computed before rendering dashboards/backlogs.
    """

    def execute(self, exporter: FilesystemExporter, context: ExportContext) -> ArtifactsDelta:
        """Execute reporting profiles.

        Args:
            exporter: Parent exporter (for assembler access)
            context: Shared export context (contains metrics in context.ctx)

        Returns:
            ArtifactsDelta with generated files.
        """
        logger: StructuredLogger | None = getattr(exporter, "_logger", None)
        generated_files: list[Path] = []

        # 1. Get profiles from config
        bundle_profiles = context.config.get("bundle_profiles") or {}

        # 2. Filter for kind='reporting'
        reporting_profiles = {
            pid: p
            for pid, p in bundle_profiles.items()
            if isinstance(p, BundleProfile) and p.kind == "reporting"
        }

        if not reporting_profiles:
            if logger:
                logger.debug("export.reporting.no_profiles", {})
            return ArtifactsDelta.empty()

        # 3. Rebuild EvalContext with enriched data
        if not context.eval_ctx:
            if logger:
                logger.warning("export.reporting.missing_eval_ctx", {})
            return ArtifactsDelta.empty()

        # Inject metrics and calculated stats into extras for the ContextBuilder
        updated_extras = dict(context.eval_ctx.extras)
        updated_extras.update(context.ctx)

        reporting_eval_ctx = EvalContext(
            plan=context.eval_ctx.plan,
            system_profile=context.eval_ctx.system_profile,
            flags=context.eval_ctx.flags,
            extras=updated_extras,
        )

        # 4. Render profiles via BundleExporter
        bundle_exporter = BundleExporter(context.assembler)

        for pid, profile in reporting_profiles.items():
            try:
                coverage = bundle_exporter.export_profile(
                    profile, reporting_eval_ctx, context.out_dir
                )
                generated_files.extend(coverage.generated_files)
                if logger:
                    logger.info("export.reporting.profile_generated", {"profile_id": pid})
            except Exception as e:
                if logger:
                    logger.error(
                        "export.reporting.profile_failed",
                        {"profile_id": pid, "error": str(e)},
                    )
                if context.strict:
                    raise

        return ArtifactsDelta.from_files(generated_files)
