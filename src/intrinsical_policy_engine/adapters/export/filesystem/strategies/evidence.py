# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Evidence collection, quality computation, and ZIP packaging strategy.

Extracted from FilesystemExporter.export() lines 535-712.
Handles:
- Evidence manifest loading/generation
- Evidence ZIP packaging
- Quality metrics computation (coverage, readiness)
- Strict mode validation (INV-01)
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC
from pathlib import Path
from typing import TYPE_CHECKING, Any

from intrinsical_policy_engine.adapters.export.base.evidence.evidence_quality import (
    compute_evidence_quality,
    dedupe_expected,
)
from intrinsical_policy_engine.adapters.export.filesystem.strategies.base import (
    ArtifactsDelta,
    ExportContext,
    ExportStrategy,
)
from intrinsical_policy_engine.adapters.frameworks.layout_loader import (
    load_framework_layout_from_path_cached,
)
from intrinsical_policy_engine.adapters.logging import StructuredLogger
from intrinsical_policy_engine.app.config.constants import METADATA_DIR, METRICS_JSON

if TYPE_CHECKING:
    from intrinsical_policy_engine.adapters.export.filesystem.filesystem_exporter import (
        FilesystemExporter,
    )
    from intrinsical_policy_engine.domain.types import Plan


class EvidenceStrategy(ExportStrategy):
    """Handles evidence collection, zipping, and quality reporting.

    This strategy:
    1. Loads or reuses the evidence manifest
    2. Creates evidence ZIP from selected articles
    3. Computes quality metrics (coverage, operational readiness)
    4. Enriches context.metrics for downstream templates
    5. Validates strict mode requirements (INV-01)
    """

    def execute(self, exporter: FilesystemExporter, context: ExportContext) -> ArtifactsDelta:
        """Execute evidence processing pipeline.

        Args:
            exporter: Parent exporter (for helpers like write_text, quality_engine)
            context: Shared export context (read-only preferred, mutates ctx for templates)

        Returns:
            ArtifactsDelta with generated files and computed metrics.

        Raises:
            ValueError: If strict mode enabled and no evidence included
            RuntimeError: If strict mode and evidence template directory missing
        """
        generated_files: list[Path] = []
        metrics_delta: dict[str, Any] = {}

        # 0. Validate evidence template directory exists
        layout = load_framework_layout_from_path_cached(Path(context.templates_dir))
        evidence_dir = layout.evidence_templates_dir
        if not evidence_dir.exists() or not evidence_dir.is_dir():
            msg = (
                f"Evidence template directory not found: {evidence_dir}. "
                "Evidence collection will be skipped. "
                "Expected location declared by framework layout."
            )
            if context.strict:
                raise RuntimeError(f"STRICT MODE: {msg}")
            else:
                logger: StructuredLogger | None = getattr(exporter, "_logger", None)
                if logger:
                    logger.warning("export.evidence.directory_missing", {"path": str(evidence_dir)})
                return ArtifactsDelta.empty()

        # 1. Load evidence map and get selected articles
        ev_map = exporter.load_evidence_map(str(context.templates_dir))
        selected_articles = exporter.selected_articles_from_plan(context.plan)

        # 2. Handle manifest (precomputed or generate new)
        manifest, zip_path = self._handle_manifest_and_zip(
            exporter,
            context,
            ev_map,
            selected_articles,
            metrics_delta,
            evidence_root=evidence_dir,
        )
        # 2b. Materialize evidence vault under evidence/
        materialized = self._materialize_evidence_vault(exporter, context, manifest)
        if materialized:
            generated_files.extend(materialized)
        if isinstance(manifest, dict):
            context.ctx["evidence_manifest"] = exporter.serializable_evidence_manifest(manifest)

        # 3. Compute quality metrics
        self._compute_quality_metrics(
            exporter, context, manifest, ev_map, selected_articles, metrics_delta
        )

        # 4. Write quality report
        self._write_quality_report(exporter, context, metrics_delta)

        # 5. Write metrics.json with aggregated metrics
        self._write_metrics_json(exporter, context)

        # 6. Strict mode validation (INV-01)
        self._validate_strict_mode(exporter, context, manifest, selected_articles)

        # 7. Track generated ZIP for fingerprinting
        if zip_path:
            generated_files.append(zip_path)

        return ArtifactsDelta(
            generated_files=tuple(generated_files),
            metrics=metrics_delta,
        )

    def _materialize_evidence_vault(  # noqa: C901
        self,
        exporter: FilesystemExporter,
        context: ExportContext,
        manifest: dict | None,
    ) -> list[Path]:
        """Copy selected evidence templates into evidence/ for human-friendly browsing."""
        if not isinstance(manifest, dict):
            return []

        included = manifest.get("included")
        if not isinstance(included, list) or not included:
            return []

        layout = load_framework_layout_from_path_cached(Path(context.templates_dir))
        root_abs = manifest.get("root_abs")
        if isinstance(root_abs, str) and root_abs:
            base_root = Path(root_abs)
        else:
            root_rel = (
                manifest.get("root")
                or layout.evidence_templates_dir.relative_to(layout.framework_dir).as_posix()
            )
            framework_dir = layout.framework_dir
            base_root = framework_dir / str(root_rel).lstrip("./")

        logger: StructuredLogger | None = getattr(exporter, "_logger", None)
        if not base_root.exists():
            msg = f"Evidence templates root not found: {base_root}"
            if context.strict:
                raise RuntimeError(msg)
            if logger:
                logger.warning("export.evidence.vault_root_missing", {"path": str(base_root)})
            return []

        evidence_root = context.out_dir / "evidence"
        evidence_root.mkdir(parents=True, exist_ok=True)

        copied: list[Path] = []
        seen: set[str] = set()
        for rel in included:
            if not isinstance(rel, str) or rel in seen:
                continue
            seen.add(rel)
            if not exporter._is_safe_relpath(rel, base_root):
                if logger:
                    logger.warning(
                        "export.evidence.vault_skip_unsafe",
                        {"relpath": rel, "base_root": str(base_root)},
                    )
                continue

            src = base_root / rel
            if not src.exists() or not src.is_file():
                if logger:
                    logger.warning(
                        "export.evidence.vault_missing_file",
                        {"path": str(src), "relpath": rel},
                    )
                continue

            dst = evidence_root / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            try:
                shutil.copy2(src, dst)
                copied.append(dst)
            except OSError as exc:
                if context.strict:
                    raise
                if logger:
                    logger.warning(
                        "export.evidence.vault_copy_failed",
                        {"error": str(exc), "path": str(dst)},
                    )

        return copied

    def _handle_manifest_and_zip(
        self,
        exporter: FilesystemExporter,
        context: ExportContext,
        ev_map: dict[str, list[dict[str, Any]]] | None,
        selected_articles: set[str],
        metrics_delta: dict[str, Any],
        *,
        evidence_root: Path,
    ) -> tuple[dict | None, Path | None]:
        """Load precomputed manifest or generate new one with ZIP."""
        cfg = context.config
        manifest: dict | None = None
        zip_path: Path | None = None

        pre = cfg.get("evidence_manifest") if isinstance(cfg, dict) else None
        if isinstance(pre, dict) and ("root" in pre) and ("included" in pre):
            manifest = pre
            zip_path = exporter.zip_from_manifest(
                context.out_dir,
                manifest,
                base_root=evidence_root,
            )
        else:
            zip_path, manifest = exporter.zip_selected_evidence(
                str(context.templates_dir),
                context.out_dir,
                selected_articles,
                ev_map,
            )

        # Store manifest in metrics delta for downstream access
        metrics_delta["evidence_manifest"] = (
            exporter.serializable_evidence_manifest(manifest)
            if isinstance(manifest, dict)
            else None
        )
        return manifest, zip_path

    def _compute_quality_metrics(
        self,
        exporter: FilesystemExporter,
        context: ExportContext,
        manifest: dict | None,
        ev_map: dict[str, list[dict[str, Any]]] | None,
        selected_articles: set[str],
        metrics_delta: dict[str, Any],
    ) -> None:
        """Compute artifact coverage, operational readiness, and TTE metrics.

        Mutates context.ctx['metrics'] with computed values (for template access).
        Also stores into metrics_delta for returning in ArtifactsDelta.
        """
        try:
            ctx = context.ctx
            cfg = context.config

            artifact_pct: dict[str, float] = {}
            artifact_by_art: dict[str, dict[str, Any]] = {}
            base_root = Path((manifest or {}).get("root") or "")
            included_list: list[str] = list((manifest or {}).get("included") or [])
            included_files = set(included_list)

            # Get or compute evidence quality report
            pre_qr = cfg.get("quality_report") if isinstance(cfg, dict) else None
            if isinstance(pre_qr, dict):
                eq = pre_qr
            else:
                eq = compute_evidence_quality(
                    base_root=base_root,
                    included=included_list,
                    selected_articles=selected_articles,
                    ev_map=ev_map,
                    quality_engine=exporter.quality_engine,
                )

            quality_by_file: dict[str, str] = eq.get("quality_by_file", {})
            missing_reasons_by_art: dict[str, list[dict[str, str]]] = eq.get(
                "missing_reasons_by_article", {}
            )

            # Compute coverage per article
            for art in sorted(selected_articles):
                coverage = self._compute_article_coverage(
                    exporter, art, ev_map, base_root, included_files, quality_by_file
                )

                artifact_pct[art] = coverage["artifact_pct"]
                artifact_by_art[art] = {
                    "delivered": coverage["delivered"],
                    "expected": coverage["expected"],
                    "missing": coverage["missing"],
                }

                # Operational coverage in metrics
                ctx.setdefault("metrics", {}).setdefault("coverage_operational_by_article", {})
                ctx["metrics"]["coverage_operational_by_article"][art] = {
                    "delivered_ready": coverage["delivered_ready"],
                    "expected": coverage["expected"],
                    "missing_ready": coverage["missing_ready"],
                }
                ctx.setdefault("metrics", {}).setdefault("coverage_operational_pct", {})
                ctx["metrics"]["coverage_operational_pct"][art] = coverage["operational_pct"]

            # --- TTE (Time-to-Evidence) Calculation ---
            tte_metrics = self._compute_tte_metrics(context.plan, selected_articles)
            ctx.setdefault("metrics", {})
            ctx["metrics"]["tte"] = tte_metrics
            metrics_delta["tte"] = tte_metrics
            # ------------------------------------------

            # Inject into context for templates
            ctx.setdefault("metrics", {})
            ctx["metrics"]["artifact_coverage_pct"] = artifact_pct
            ctx["metrics"]["artifact_coverage_by_article"] = artifact_by_art
            ctx["metrics"]["missing_reasons_by_article"] = missing_reasons_by_art
            ctx["metrics"]["quality_by_file"] = quality_by_file

            # Top-level values for template convenience
            ctx["artifact_coverage_pct"] = artifact_pct
            ctx["artifact_coverage_by_article"] = artifact_by_art
            ctx["missing_reasons_by_article"] = missing_reasons_by_art
            ctx["coverage_operational_by_article"] = ctx["metrics"].get(
                "coverage_operational_by_article", {}
            )
            ctx["coverage_operational_pct"] = ctx["metrics"].get("coverage_operational_pct", {})
            ctx["quality_by_file"] = quality_by_file
            ctx["tte"] = tte_metrics  # Alias for TTE

            # Quality summary counts
            q_counts = {"ready": 0, "draft": 0, "placeholder": 0}
            for q in quality_by_file.values():
                if q in q_counts:
                    q_counts[q] += 1
            ctx["evidence_quality_summary"] = q_counts

            # Store for quality report writing
            metrics_delta["quality_by_file"] = quality_by_file
            metrics_delta["missing_reasons_by_article"] = missing_reasons_by_art

        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as e:
            # Expected I/O or parsing errors - recoverable in non-strict mode
            # R3P0 Fase 2: Fail-hard in strict mode, write ERROR_LOG.md in tolerant mode
            # Reference: docs/invariants/ENGINE-ARCHITECTURE-v1.md (Dominio 4)
            # "Emitir informes de calidad que pueden gatear exportaciones"
            if context.strict:
                raise ValueError(f"Evidence quality computation failed in strict mode: {e}") from e
            # Non-strict: Write error to ERROR_LOG.md for audit trail
            self._write_error_log(context, "evidence_quality", str(e))

        except (KeyError, TypeError, AttributeError) as e:
            # Programming errors - these should not be silently swallowed
            # Log the error with full context for debugging
            logger: StructuredLogger | None = getattr(exporter, "_logger", None)
            if logger:
                logger.error(
                    "export.evidence.quality_computation_bug",
                    {
                        "error_type": type(e).__name__,
                        "error": str(e),
                        "hint": "This is likely a bug in quality computation. Please report.",
                    },
                )
            if context.strict:
                raise RuntimeError(
                    f"Evidence quality computation bug (strict mode): {type(e).__name__}: {e}"
                ) from e
            # Non-strict: Still write to error log, but re-raise as ValueError for visibility
            self._write_error_log(
                context,
                "evidence_quality",
                f"INTERNAL ERROR ({type(e).__name__}): {e} - This may indicate a bug.",
            )

    def _compute_article_coverage(
        self,
        exporter: FilesystemExporter,
        art: str,
        ev_map: dict[str, list[dict[str, Any]]] | None,
        base_root: Path,
        included_files: set[str],
        quality_by_file: dict[str, str],
    ) -> dict[str, Any]:
        """Compute coverage metrics for a single article."""
        expected_entries = dedupe_expected(list((ev_map or {}).get(art, []) or []))
        expected_count = sum(1 for e in expected_entries if e.get("required", True))

        delivered = 0
        delivered_ready = 0
        missing: list[str] = []
        missing_ready: list[str] = []

        for e in expected_entries:
            p = str(e.get("path"))
            req = bool(e.get("required", True))
            if not req:
                continue

            if p.endswith("/"):
                # Directory requirement
                ok = exporter._dir_requirement_met(base_root, included_files, p)
                op_ok = self._check_dir_operational_ready(
                    exporter, base_root, included_files, quality_by_file, p
                )
            else:
                # File requirement
                ok = (p in included_files) and exporter._is_valid_evidence_file(base_root / p)
                op_ok = self._check_file_operational_ready(
                    exporter, base_root, included_files, quality_by_file, p
                )

            if ok:
                delivered += 1
            else:
                missing.append(p)

            if op_ok:
                delivered_ready += 1
            else:
                missing_ready.append(p)

        return {
            "delivered": delivered,
            "delivered_ready": delivered_ready,
            "expected": expected_count,
            "missing": missing,
            "missing_ready": missing_ready,
            "artifact_pct": round(delivered / expected_count, 4) if expected_count > 0 else 0.0,
            "operational_pct": (
                round(delivered_ready / expected_count, 4) if expected_count > 0 else 0.0
            ),
        }

    def _check_dir_operational_ready(
        self,
        exporter: FilesystemExporter,
        base_root: Path,
        included_files: set[str],
        quality_by_file: dict[str, str],
        path: str,
    ) -> bool:
        """Check if directory meets operational readiness (README.md or manifest.json)."""
        readme_rel = f"{path}README.md"
        manifest_rel = f"{path}manifest.json"

        if readme_rel in included_files:
            return quality_by_file.get(readme_rel) == "ready"
        elif manifest_rel in included_files:
            return exporter._evidence_quality(base_root / manifest_rel) == "ready"
        return False

    def _check_file_operational_ready(
        self,
        exporter: FilesystemExporter,
        base_root: Path,
        included_files: set[str],
        quality_by_file: dict[str, str],
        path: str,
    ) -> bool:
        """Check if file meets operational readiness."""
        if path not in included_files:
            return False

        if path.lower().endswith(".md"):
            return quality_by_file.get(path) == "ready"
        return exporter._evidence_quality(base_root / path) == "ready"

    def _write_error_log(self, context: ExportContext, phase: str, error_msg: str) -> None:
        """Write error to ERROR_LOG.md for audit trail in non-strict mode.

        R3P0 Fase 2: Creates an audit-friendly error log when failures occur
        in tolerant mode, ensuring traceability without breaking the export.

        Reference: docs/invariants/ENGINE-ARCHITECTURE-v1.md (Dominio 4 - QualityEngine)
        """
        from datetime import datetime

        error_log_path = context.out_dir / "ERROR_LOG.md"

        # Append to existing log or create new one
        try:
            existing = error_log_path.read_text(encoding="utf-8") if error_log_path.exists() else ""
        except OSError:
            existing = ""

        timestamp = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        entry = f"""
## Error in {phase}

- **Timestamp**: {timestamp}
- **Phase**: {phase}
- **Mode**: tolerant (non-strict)
- **Error**: {error_msg}

---
"""
        try:
            if not existing:
                header = "# Export Error Log\n\nErrors encountered during export (tolerant mode).\n"
                error_log_path.write_text(header + entry, encoding="utf-8")
            else:
                error_log_path.write_text(existing + entry, encoding="utf-8")
        except OSError:
            # Audit WIN-1: Error log write failure is not critical
            # Note: Cannot log here as we're already in error handling and have no exporter ref
            pass

    def _write_quality_report(
        self, exporter: FilesystemExporter, context: ExportContext, metrics_delta: dict[str, Any]
    ) -> None:
        """Write evidence_quality.json (always creates file for consistency).

        O-04: Writes to _metadata/ subdirectory (TREE-SPECS-v1.md).
        """
        try:
            pre_qr = (
                context.config.get("quality_report") if isinstance(context.config, dict) else None
            )
            if isinstance(pre_qr, dict):
                return

            quality_by_file = metrics_delta.get("quality_by_file", {})
            missing_reasons = metrics_delta.get("missing_reasons_by_article", {})

            # Always write the file for consistency (even if empty)
            quality_report = {
                "quality_by_file": quality_by_file,
                "missing_reasons_by_article": missing_reasons,
            }
            # O-04: Write to _metadata/ subdirectory
            metadata_dir = context.out_dir / METADATA_DIR
            metadata_dir.mkdir(parents=True, exist_ok=True)
            qpath = metadata_dir / "evidence_quality.json"
            exporter.write_text(qpath, json.dumps(quality_report, indent=2, ensure_ascii=False))
        except OSError as exc:
            # Audit WIN-1: Log write failures for observability
            logger: StructuredLogger | None = getattr(exporter, "_logger", None)
            if logger:
                logger.warning(
                    "export.evidence.quality_report_write_failed",
                    {"error": str(exc), "path": str(qpath)},
                )
            if context.strict:
                raise

    def _write_metrics_json(self, exporter: FilesystemExporter, context: ExportContext) -> None:
        """Write metrics.json with aggregated export metrics.

        O-04: Writes to _metadata/ subdirectory (TREE-SPECS-v1.md).
        """
        logger: StructuredLogger | None = getattr(exporter, "_logger", None)
        try:
            metadata_dir = context.out_dir / METADATA_DIR
            metadata_dir.mkdir(parents=True, exist_ok=True)
            metrics_path = metadata_dir / METRICS_JSON
            ctx = context.ctx

            metrics_payload = {
                "coverage_invb1": ctx.get("coverage_invb1", {}),
                "coverage_invb1_actions_required": ctx.get("coverage_invb1_actions_required", 0),
                "coverage_invb1_actions_covered": ctx.get("coverage_invb1_actions_covered", 0),
                "coverage_invb1_actions_missing": ctx.get("coverage_invb1_actions_missing", 0),
                "coverage_invb1_actions_pct": ctx.get("coverage_invb1_actions_pct", 0.0),
                "coverage_invb1_evidences_required": ctx.get(
                    "coverage_invb1_evidences_required", 0
                ),
                "coverage_invb1_evidences_covered": ctx.get("coverage_invb1_evidences_covered", 0),
                "coverage_invb1_evidences_missing": ctx.get("coverage_invb1_evidences_missing", 0),
                "coverage_invb1_evidences_pct": ctx.get("coverage_invb1_evidences_pct", 0.0),
                "artifact_coverage_by_article": ctx.get("artifact_coverage_by_article", {}),
                "artifact_coverage_pct": ctx.get("artifact_coverage_pct", {}),
                "coverage_operational_by_article": ctx.get("coverage_operational_by_article", {}),
                "coverage_operational_pct": ctx.get("coverage_operational_pct", {}),
                "quality_by_file": ctx.get("quality_by_file", {}),
                "evidence_quality_summary": ctx.get("evidence_quality_summary", {}),
                "tte": ctx.get("metrics", {}).get("tte") or ctx.get("tte"),
            }

            exporter.write_text(
                metrics_path, json.dumps(metrics_payload, indent=2, ensure_ascii=False)
            )
        except OSError as exc:
            # Audit WIN-1: Log write failures for observability
            if logger:
                logger.warning(
                    "export.evidence.metrics_write_failed",
                    {"error": str(exc), "path": str(metrics_path)},
                )
            if context.strict:
                raise

    # [DELETED] _rerender_metric_dependent_templates
    # FIX R3: This method has been removed. Templates that depend on metrics
    # are now handled declaratively via ReportingStrategy and 'kind: reporting'
    # profiles in bundle configuration. This eliminates hardcoded filenames.

    def _validate_strict_mode(
        self,
        exporter: FilesystemExporter,
        context: ExportContext,
        manifest: dict | None,
        selected_articles: set[str],
    ) -> None:
        """Validate strict mode requirements (INV-01).

        In strict mode, export must include at least one evidence file.
        This ensures compliance bundles are never empty.

        Raises:
            ValueError: If strict mode and no evidence included
        """
        if not context.strict:
            return

        inc = len((manifest or {}).get("included", [])) if isinstance(manifest, dict) else 0
        if inc == 0:
            logger: StructuredLogger | None = getattr(exporter, "_logger", None)
            if logger:
                logger.warning(
                    "export.filesystem.strict_no_evidence",
                    {"articles": len(selected_articles)},
                )
            raise ValueError("filesystem: strict mode requires at least one evidence included")

    def _compute_tte_metrics(self, plan: Plan, selected_articles: set[str]) -> dict[str, Any]:
        """Compute Time-to-Evidence (TTE) metrics based on active actions.

        Aggregates effort estimates (technical, documentation, external) for all
        active actions in the plan. Used for "Audit Readiness" dashboard.

        Returns:
            Dictionary with aggregated hours (CU) and detailed breakdown.
        """
        total_technical = 0
        total_documentation = 0
        total_external = 0
        actions_count = 0

        # Extract actions from plan
        actions_list = plan.get("actions", [])
        if not actions_list:
            return {
                "total_hours": 0,
                "breakdown": {"technical": 0, "documentation": 0, "external": 0},
                "actions_count": 0,
            }

        # We need the full action definitions to get effort estimates.
        # Since 'plan' only contains derived action IDs/titles, we need to access
        # the full definitions from the actions_meta list if available, or fall back to defaults.
        # 'actions_meta' is typically populated by assess_service.py
        actions_meta = plan.get("actions_meta", [])

        # Map IDs for quick lookup
        meta_map = {a.get("id"): a for a in actions_meta}

        for action_entry in actions_list:
            # action_entry might be just ID string or dict with 'id'
            aid = action_entry.get("id") if isinstance(action_entry, dict) else action_entry

            if not aid:
                continue

            # Look up full definition
            action_def = meta_map.get(aid)
            if not action_def:
                continue

            # Get effort object (defaults to 0 if missing)
            effort = action_def.get("effort", {})
            if not isinstance(effort, dict):
                effort = {}
            effort_vals = (
                effort.get("technical", 0),
                effort.get("documentation", 0),
                effort.get("external", 0),
            )
            effort_is_zero = all((isinstance(v, (int, float)) and v == 0) for v in effort_vals)

            # If effort is missing/empty or all-zero but T-shirt size is present, use heuristics
            if (not effort or effort_is_zero) and "effort_t_shirt" in action_def:
                size = action_def.get("effort_t_shirt", "M")
                # Heuristics: S=4h, M=16h, L=40h, XL=100h (split 50/50 tech/doc)
                heuristic_map = {"S": 4, "M": 16, "L": 40, "XL": 100}
                hours = heuristic_map.get(size, 16)
                total_technical += hours // 2
                total_documentation += hours // 2
            else:
                total_technical += effort.get("technical", 0)
                total_documentation += effort.get("documentation", 0)
                total_external += effort.get("external", 0)

            actions_count += 1

        total_hours = total_technical + total_documentation + total_external

        return {
            "total_hours": total_hours,
            "breakdown": {
                "technical": total_technical,
                "documentation": total_documentation,
                "external": total_external,
            },
            "actions_count": actions_count,
            "unit": "Compliance Units (CU ~= 1 hour)",
        }
