# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Filesystem exporter: Orchestrator using the Strategy Pattern.

This is the refactored version using decomposed strategies.
See strategies/ submodule for the actual export logic.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from intrinsical_policy_engine.adapters.export.base.exporters.base_exporter import BaseExporter
from intrinsical_policy_engine.adapters.export.filesystem.strategies.base import (
    ExportContext,
    ExportStrategy,
)
from intrinsical_policy_engine.adapters.export.filesystem.strategies.bundle_profile import (
    BundleProfileStrategy,
)
from intrinsical_policy_engine.adapters.export.filesystem.strategies.evidence import (
    EvidenceStrategy,
)

# All export content is now handled via BundleExporter and bundle profiles
from intrinsical_policy_engine.adapters.export.filesystem.strategies.manifest import (
    ManifestStrategy,
)
from intrinsical_policy_engine.adapters.export.filesystem.strategies.omissions import (
    OmissionsStrategy,
)
from intrinsical_policy_engine.adapters.export.filesystem.strategies.reporting import (
    ReportingStrategy,
)
from intrinsical_policy_engine.adapters.quality.engine import QualityEngine
from intrinsical_policy_engine.app.config.context import build_artifact_context
from intrinsical_policy_engine.app.rendering.templating import ArtifactAssembler
from intrinsical_policy_engine.common.sanitization import SanitizationMode, sanitize_payload
from intrinsical_policy_engine.domain.types import Plan


class FilesystemExporter(BaseExporter):
    """Render artifact templates and compile evidence bundles on disk.

    Acts as a context builder and strategy orchestrator.
    All export logic is delegated to strategies in the strategies/ submodule.
    """

    def __init__(self) -> None:
        """Initialize the quality engine and strategy pipeline."""
        super().__init__()
        self.quality_engine = QualityEngine()
        # Assembler is initialized per-export to respect strict/tolerant config
        self.assembler: ArtifactAssembler | None = None

        # Strategies are initialized in export() based on config
        self._strategies: list[ExportStrategy] = []

    def export(
        self,
        plan: Plan,
        templates_dir: str,
        out_dir: str,
        wizard_answers: dict[str, Any] | None = None,
    ) -> None:
        """Execute all configured export strategies.

        Orchestrates the export pipeline by:
        1. Preparing infrastructure (directories, assembler, config)
        2. Building template context from plan
        3. Injecting regulatory metadata and evidence map
        4. Creating shared ExportContext
        5. Executing strategies in order (BundleProfile, Evidence, Reporting, Omissions, Manifest)

        Args:
            plan: The compliance plan dictionary to export.
            templates_dir: Path to template files directory.
            out_dir: Output directory path where artifacts will be written.
            wizard_answers: Optional wizard answers dictionary for export context.
                If omitted, export proceeds without wizard answers.

        Raises:
            ValueError: If strict mode is enabled and templates_dir is missing or invalid.

        Note:
            Strategy execution order is CRITICAL and must not be changed:
            BundleProfile -> Evidence -> Reporting -> Omissions -> Manifest
            Altering this order will break metric-dependent templates.
        """
        # 1. Prepare Infrastructure
        out_path = Path(out_dir)
        out_path.mkdir(parents=True, exist_ok=True)
        t_path = Path(templates_dir)

        cfg = self._config or {}
        # Default to non-strict unless the caller requests strict validation.
        strict_flag = bool(cfg.get("strict", False))
        export_metrics: dict[str, Any] = {}
        if isinstance(cfg, dict) and isinstance(cfg.get("export_metrics"), dict):
            export_metrics = dict(cfg.get("export_metrics") or {})

        # Validate templates directory in strict mode
        if strict_flag and (not t_path.exists() or not t_path.is_dir()):
            raise ValueError(f"filesystem: templates_dir not found: {t_path}")

        self.assembler = ArtifactAssembler(t_path, strict=strict_flag)

        # 2. Build Context (Domain -> Adapter translation)
        ctx = self.build_context(plan)

        # Inject regulatory metadata (now uses bundle from config, not filesystem)
        # Bundle is passed via setup() config by orchestrator (PR1 fix)
        bundle = cfg.get("bundle")
        ctx["_bundle"] = bundle
        self._inject_regulatory_meta(ctx, plan)

        # Red Team Fix (Auditor): Propagate evidence_map to context for audit trail hashing
        if bundle and hasattr(bundle, "evidence_map") and bundle.evidence_map:
            ctx["evidence_map"] = bundle.evidence_map
            # Calculate hash here since build_artifact_context was called before evidence_map inject
            import hashlib
            import json as json_mod

            evidence_hash = hashlib.sha256(
                json_mod.dumps(bundle.evidence_map, sort_keys=True).encode("utf-8")
            ).hexdigest()  # Full SHA256 for cryptographic integrity (Red Team Fix R3)
            if "audit" not in ctx:
                ctx["audit"] = {}
            ctx["audit"]["evidence_map_sha256"] = evidence_hash

        # Inject wizard answers if available
        wiz_answers = wizard_answers or cfg.get("wizard_answers") or {}
        include_raw_answers = bool(cfg.get("include_raw_answers", False))
        wizard_answers_sanitized = bool(cfg.get("wizard_answers_sanitized", False))

        if wiz_answers and isinstance(wiz_answers, dict):
            if include_raw_answers or wizard_answers_sanitized:
                safe_wiz_answers = dict(wiz_answers)
            else:
                safe_wiz_answers = sanitize_payload(wiz_answers, mode=SanitizationMode.HASH)
            ctx["wizard_answers"] = dict(safe_wiz_answers)
            ctx["answers"] = dict(safe_wiz_answers)
            wiz_answers = safe_wiz_answers

        # 3. Create Shared Execution Context
        export_ctx = ExportContext(
            plan=plan,
            ctx=ctx,
            out_dir=out_path,
            templates_dir=t_path,
            assembler=self.assembler,
            config=cfg,
            strict=strict_flag,
            generated_files=[],
            metrics=export_metrics,
        )

        # 4. Initialize Strategies
        # =========================================================================
        # CRITICAL: EXECUTION ORDER MATTERS!
        # =========================================================================
        # The order of strategies is CRITICAL and must NOT be changed:
        # 1. BundleProfileStrategy - Generates base content (skips 'reporting' kind)
        # 2. EvidenceStrategy      - Computes metrics from generated content
        # 3. ReportingStrategy     - Uses metrics to render dashboards/backlogs
        # 4. ManifestStrategy      - Seals everything with checksums/fingerprints
        #
        # Altering this order WILL BREAK metric-dependent templates.
        # Reference: docs/invariants/ENGINE-ARCHITECTURE-v1.md (FilesystemExporter as orquestador)
        # =========================================================================

        strategies: list[ExportStrategy] = [
            BundleProfileStrategy(),  # 1. Content generation (Technical/Legal)
            EvidenceStrategy(),  # 2. Evidence zips & metrics calculation
            ReportingStrategy(),  # 3. Reporting (Dashboards/Backlogs using metrics)
            OmissionsStrategy(),  # 3b. Governance: Mandatory Omissions Report (New v1)
            ManifestStrategy(),  # 4. Sealing: fingerprint, checksums, index
        ]

        self._strategies = strategies

        # Write wizard_answers.json before the pipeline so the manifest hashes it.
        # O-04: Write to _metadata/ subdirectory (plumbing files)
        if wiz_answers:
            import json as json_mod2

            from intrinsical_policy_engine.app.config.constants import METADATA_DIR

            metadata_dir = out_path / METADATA_DIR
            metadata_dir.mkdir(parents=True, exist_ok=True)
            answers_path = metadata_dir / "wizard_answers.json"
            answers_path.write_text(
                json_mod2.dumps(wiz_answers, indent=2, sort_keys=True, ensure_ascii=False),
                encoding="utf-8",
            )
            export_ctx.generated_files.append(answers_path)

        # 5. Execute Pipeline (Collect and apply deltas)
        for strategy in self._strategies:
            delta = strategy.execute(self, export_ctx)
            export_ctx.apply_delta(delta)

    def build_context(self, plan: Plan) -> dict[str, Any]:
        """Override to use the richer artifact context."""
        cfg = getattr(self, "_config", {}) or {}
        extra_metrics = cfg.get("export_metrics") if isinstance(cfg, dict) else None
        framework_path = None
        if isinstance(cfg, dict):
            framework_path = cfg.get("framework_path")
        if framework_path is not None and not isinstance(framework_path, Path):
            framework_path = Path(str(framework_path))
        return build_artifact_context(
            cast(dict, plan),
            strict=False,
            extra_metrics=extra_metrics,
            framework_path=framework_path,
        )

    def _inject_regulatory_meta(self, ctx: dict[str, Any], plan: Plan) -> None:
        """Inject regulatory metadata from bundle's RulesContract.

        This method no longer reads rules.yml directly. Instead, it uses the
        already-loaded regulatory_meta from the RulesContract, which was parsed
        during bundle loading. This ensures:
        1. No silent failures (errors surface at load time, not export time)
        2. Single source of truth (rules.yml is only read once)
        3. Proper layering (adapter doesn't do domain parsing)
        """
        from intrinsical_policy_engine.domain.services.regulatory_meta import (
            build_regulatory_warnings,
            extract_regulatory_meta,
            regulatory_meta_to_dict,
        )

        # Get bundle from context (set by caller or strategies)
        bundle = ctx.get("_bundle")
        if not bundle:
            return

        # Extract from rules contract (already loaded during bundle load)
        rules_data = getattr(bundle, "rules", None)
        if not rules_data:
            return

        # Use the domain service to extract metadata
        rules_dict = rules_data.model_dump() if hasattr(rules_data, "model_dump") else {}
        reg_meta = extract_regulatory_meta(rules_dict)
        if not reg_meta:
            return

        ctx["regulatory_version"] = regulatory_meta_to_dict(reg_meta)

        # Build warnings based on plan context
        routing_route = None
        if isinstance(plan, dict):
            routing_route = plan.get("routing", {}).get("route")

        warnings = build_regulatory_warnings(reg_meta, routing_route)
        if warnings:
            ctx["regulatory_warnings"] = list(warnings.warnings)

    # --- Helper methods needed by strategies ---

    def _render(self, templates_dir: str, template_name: str, ctx: dict[str, Any]) -> str:
        """Render a single template using the assembler."""
        if not self.assembler:
            cfg = getattr(self, "_config", {}) or {}
            strict_flag = bool(cfg.get("strict", True))
            self.assembler = ArtifactAssembler(Path(templates_dir), strict=strict_flag)

        cfg = getattr(self, "_config", {}) or {}
        answers = cfg.get("wizard_answers")
        return self.assembler.assemble(template_name, ctx, answers)

    def _dir_requirement_met(
        self, base_root: Path, included_files: set[str], dir_path: str
    ) -> bool:
        """Check if directory requirement is met (has README.md or manifest.json)."""
        return self.quality_engine.dir_requirement_met(base_root, included_files, dir_path)

    def _is_valid_evidence_file(self, path: Path) -> bool:
        """Delegate to quality engine for file validation."""
        return self.quality_engine.is_valid_file(path)

    def _evidence_quality(self, path: Path) -> str:
        """Delegate to quality engine for quality classification."""
        return self.quality_engine.classify_file(path)
