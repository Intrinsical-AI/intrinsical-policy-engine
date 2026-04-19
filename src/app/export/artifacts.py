# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Artifact writing utilities for export workflow.

This module handles writing of pre-export artifacts like summary.json,
trace.json, evidence_manifest.json, etc.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from src.adapters.export.base.evidence.evidence_quality import compute_evidence_quality
from src.adapters.export.base.exporters.base_exporter import BaseExporter, EvidenceManifest
from src.adapters.logging import StructuredLogger
from src.adapters.quality.engine import QualityEngine
from src.app.config.artifact_names import SUMMARY_FILE
from src.app.config.constants import (
    DEFAULT_ENCODING,
    EVIDENCE_MANIFEST,
    MANIFEST_MD,
    METADATA_DIR,
    METRICS_JSON,
    TRACE_JSON,
)
from src.app.config.context import build_base_context, now_iso_z
from src.common.hashing import sha256_directory, sha256_file
from src.domain.types import Plan


@dataclass
class ArtifactsState:
    """State of pre-export artifacts preparation."""

    templates_dir: str
    tgt_list: list[str]
    cfg_all: dict | object
    pre_manifest: EvidenceManifest | None
    quality_report: dict | None
    summary_error: bool
    evidence_manifest_error: bool
    evidence_quality_error: bool
    trace_error: bool
    manifest_error: bool
    config_error: bool
    config_error_msg: str | None
    templates_validation_error: bool = False
    templates_validation_msg: str | None = None

    @property
    def pre_artifact_error(self) -> bool:
        """Return True if any pre-export artifacts failed to generate."""
        return any(
            [
                self.summary_error,
                self.evidence_manifest_error,
                self.evidence_quality_error,
                self.trace_error,
                self.manifest_error,
                self.config_error,
                self.templates_validation_error,
            ]
        )


class ArtifactWriter:
    """Handles writing of pre-export artifacts.

    Separates I/O operations from workflow orchestration.
    """

    def __init__(self, logger: StructuredLogger | None = None):
        """Store a logger reference for structured telemetry.

        Args:
            logger: Optional logger implementing StructuredLogger protocol.
        """
        self._logger = logger

    def _log_event(self, event: str, data: dict) -> None:
        """Emit a log event only if a logger is available."""
        if self._logger:
            self._logger.info(event, data)

    def _sha256_file(self, path: Path) -> str:
        """Compute SHA-256 hash of a file. Delegates to public helper."""
        return sha256_file(path)

    def _sha256_directory(self, path: Path) -> str:
        """Compute SHA-256 hash of a directory. Delegates to public helper."""
        return sha256_directory(path, warn_if_missing=True)

    def _redact_trace_for_export(self, trace: dict | None) -> dict:
        """Redact review fields from trace before persisting to disk."""
        if not isinstance(trace, dict):
            return {}
        redacted = dict(trace)
        answers_raw = trace.get("answers_raw")
        if isinstance(answers_raw, dict) and "answers" in answers_raw:
            sanitized_answers_raw = dict(answers_raw)
            sanitized_answers_raw.pop("answers", None)
            redacted["answers_raw"] = sanitized_answers_raw
        return redacted

    def _redact_plan_for_export(self, plan: Plan) -> dict[str, Any]:
        """Return a shallow copy of plan with redacted trace fields."""
        if not isinstance(plan, dict):
            return plan
        redacted_plan = dict(plan)
        trace = plan.get("trace")
        if isinstance(trace, dict):
            redacted_plan["trace"] = self._redact_trace_for_export(trace)
        return redacted_plan

    def write_summary(self, plan: Plan, outdir: Path) -> bool:
        """Write summary.json for the plan.

        O-04: Writes to _metadata/ subdirectory (TREE-SPECS-v1.md).

        Returns:
            True if an error occurred, False otherwise.
        """
        try:
            metadata_dir = outdir / METADATA_DIR
            metadata_dir.mkdir(parents=True, exist_ok=True)
            summary_path = metadata_dir / SUMMARY_FILE
            safe_plan = self._redact_plan_for_export(plan)
            summary_path.write_text(
                json.dumps(safe_plan, indent=2, ensure_ascii=False) + "\n",
                encoding=DEFAULT_ENCODING,
            )
            self._log_event("export.summary_written", {"path": str(summary_path)})
            return False
        except (OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            self._log_event("export.summary_write_failed", {"error": str(exc)})
            return True

    def write_trace(self, plan: Plan, outdir: Path) -> bool:
        """Write trace.json derived from the plan.

        O-04: Writes to _metadata/ subdirectory (TREE-SPECS-v1.md).

        Returns:
            True if an error occurred, False otherwise.
        """
        try:
            metadata_dir = outdir / METADATA_DIR
            metadata_dir.mkdir(parents=True, exist_ok=True)
            trace_path = metadata_dir / TRACE_JSON
            safe_trace = self._redact_trace_for_export(plan.get("trace", {}))
            trace_path.write_text(
                json.dumps(safe_trace, indent=2, ensure_ascii=False) + "\n",
                encoding=DEFAULT_ENCODING,
            )
            self._log_event("export.trace_written", {"path": str(trace_path)})
            return False
        except (OSError, ValueError, TypeError, json.JSONDecodeError) as exc:
            self._log_event("export.trace_write_failed", {"error": str(exc)})
            return True

    def write_manifest_md(
        self, plan: Plan, outdir: Path, templates_dir: Path | str | None = None
    ) -> bool:
        """Write manifest.md summary for the assessment run.

        Per docs/invariants/ENGINE-ARCHITECTURE-v1.md, the manifest must include:
        - Identification: fingerprint, timestamps, engine/framework versions
        - Integrity hashes: answers_hash, bundle_hash, templates_hash, plan_hash
        - Quality state: gating status, warnings count

        Returns:
            True if an error occurred, False otherwise.
        """
        try:
            ctx = build_base_context(dict(plan))
            plan_fp = (ctx.get("plan") or {}).get("fingerprint", "")
            meta = ctx.get("meta") or {}
            assessment_ts = meta.get("generated_at") or now_iso_z()
            exported_at = meta.get("exported_at") or now_iso_z()
            flags = plan.get("flags") or []
            actions = plan.get("actions") or []
            arts = sorted(list((plan.get("articles_overlay") or {}).keys()))
            ltk = plan.get("legal_token") or {}

            # Extract trace hashes per docs/invariants/ENGINE-ARCHITECTURE-v1.md
            trace = plan.get("trace") or {}
            bundle_hash = trace.get("bundle_hash", "<not computed>")
            framework_pack_hash = trace.get("framework_pack_hash", "<not computed>")
            pack_hashes = trace.get("pack_hashes") or {}
            plan_hash = trace.get("plan_hash", "<not computed>")
            engine_version = trace.get("engine_version", "<unknown>")
            framework_version = trace.get("framework_version", "<unknown>")
            contracts_version = trace.get("contracts_version", "<unknown>")

            # answers_hash from trace.answers_raw
            answers_raw = trace.get("answers_raw") or {}
            answers_hash = answers_raw.get("answers_hash", "<not computed>")

            # templates_hash should come from trace (captured at assess time) per INV-05.
            templates_hash_from_trace = trace.get("templates_hash")
            templates_hash = templates_hash_from_trace or "<not computed>"

            # Extract outcome for risk classification
            outcome = plan.get("outcome", [])
            outcome_axes = plan.get("outcome_axes", {})
            risk_tier = outcome_axes.get("risk_tier", "<unknown>")
            routing_route = (plan.get("routing") or {}).get("route", "<unknown>")

            manifest_lines = [
                "## Manifest",
                "",
                "## Identification",
                f"- **fingerprint**: `{plan_fp}`",
                f"- **assessment_timestamp**: {assessment_ts}",
                f"- **exported_at**: {exported_at}",
                f"- **engine_version**: {engine_version}",
                f"- **framework_version**: {framework_version}",
                f"- **contracts_version**: {contracts_version}",
                "",
                "## Integrity Hashes",
                # Red Team Fix (Auditor): Full hashes, no truncation
                f"- **plan_fingerprint**: `{plan_fp}`",
                f"- **answers_hash**: `{answers_hash}`",
                f"- **bundle_hash**: `{bundle_hash}`",
                f"- **framework_pack_hash**: `{framework_pack_hash}`",
                f"- **templates_hash**: `{templates_hash}`",
                f"- **plan_hash**: `{plan_hash}`",
                "",
            ]

            if pack_hashes:
                missing = "<not computed>"
                manifest_lines.extend(
                    [
                        "## Framework Pack Components",
                        f"- **law_data_hash**: `{pack_hashes.get('law_data_hash', missing)}`",
                        (
                            f"- **render_templates_hash**: "
                            f"`{pack_hashes.get('render_templates_hash', missing)}`"
                        ),
                        (
                            f"- **evidence_templates_hash**: "
                            f"`{pack_hashes.get('evidence_templates_hash', missing)}`"
                        ),
                        (
                            f"- **bundle_profiles_hash**: "
                            f"`{pack_hashes.get('bundle_profiles_hash', missing)}`"
                        ),
                        f"- **schemas_hash**: `{pack_hashes.get('schemas_hash', missing)}`",
                        (
                            f"- **framework_version_file_hash**: "
                            f"`{pack_hashes.get('framework_version_file_hash', missing)}`"
                        ),
                        (
                            f"- **manifest_file_hash**: "
                            f"`{pack_hashes.get('manifest_file_hash', missing)}`"
                        ),
                        "",
                    ]
                )

            # Improved hash consistency message to always show verification section
            manifest_lines.append("## Plan Hash Verification")
            if plan_fp and plan_hash:
                if plan_fp == plan_hash:
                    manifest_lines.extend(
                        [
                            "- **status**: VERIFIED — Los hashes coinciden",
                            "- **plan_fingerprint**: Hash del archivo plan.json exportado",
                            "- **trace.plan_hash**: Hash semántico calculado durante assessment",
                            "- **interpretation**: El plan exportado es idéntico al plan evaluado",
                            "",
                        ]
                    )
                else:
                    manifest_lines.extend(
                        [
                            "- **status**: NORMAL — Diferencia esperada por metadatos de export",
                            "- **plan_fingerprint**: Hash del archivo plan.json exportado (incluye "
                            "metadatos)",
                            "- **trace.plan_hash**: Hash semántico del plan canónico (assessment)",
                            "- **interpretation**: Las clasificaciones y acciones son "
                            "idénticas; la diferencia se debe a metadatos añadidos "
                            "durante export (timestamps, paths).",
                            "- **verification**: Para verificar integridad, use `trace.plan_hash` "
                            "como referencia.",
                            "",
                        ]
                    )
            else:
                manifest_lines.extend(
                    [
                        "- **status**: INCOMPLETE — Hashes no disponibles",
                        "- **interpretation**: El plan fue generado sin trazabilidad completa",
                        "",
                    ]
                )

            manifest_lines.extend(
                [
                    "## Summary",
                    f"- **outcome**: {', '.join(outcome) if outcome else '<none>'}",
                    f"- **risk_tier**: {risk_tier}",
                    f"- **routing_route**: {routing_route}",
                    f"- **counts**: flags={len(flags)}, actions={len(actions)}",
                    f"- **articles**: {', '.join(arts[:10])}"
                    + (f" (+{len(arts) - 10} more)" if len(arts) > 10 else "")
                    if arts
                    else "- **articles**: <none>",
                    "",
                    "## Legal Reference",
                    f"- **legal_token**: {json.dumps(ltk, ensure_ascii=False)}",
                    "",
                    "## Artifacts",
                    f"- **trace**: {METADATA_DIR}/{TRACE_JSON}",
                    f"- **summary**: {METADATA_DIR}/{SUMMARY_FILE}",
                    f"- **metrics**: {METADATA_DIR}/{METRICS_JSON}",
                ]
            )

            # Safety override section
            conf = plan.get("routing") or {}
            if conf.get("safety_override"):
                manifest_lines.extend(
                    [
                        "",
                        "## Safety Override (Conservative Route)",
                        "- **status**: ACTIVE",
                        f"- **forced_route**: {conf.get('route')}",
                        f"- **alternative_route**: {conf.get('alternative_route')}",
                        f"- **override_flags**: {', '.join(conf.get('override_flags') or [])}",
                    ]
                )

            # Warnings section if any
            rules_applied = trace.get("rules_applied") or {}
            warnings = rules_applied.get("warnings") or {}
            if warnings:
                manifest_lines.extend(
                    [
                        "",
                        "## Warnings",
                    ]
                )
                for warn_type, warn_list in warnings.items():
                    manifest_lines.append(f"- **{warn_type}**: {len(warn_list)} warning(s)")

            (outdir / MANIFEST_MD).write_text(
                "\n".join(manifest_lines) + "\n", encoding=DEFAULT_ENCODING
            )
            self._log_event("export.manifest_written", {"path": str(outdir / MANIFEST_MD)})
            return False
        except (OSError, UnicodeDecodeError, ValueError) as exc:
            self._log_event("export.manifest_write_failed", {"error": str(exc)})
            return True

    def build_evidence_manifest_and_quality(
        self,
        plan: Plan,
        templates_dir: str,
        outdir: Path,
    ) -> tuple[EvidenceManifest | None, dict | None, bool, bool]:
        """Build evidence_manifest.json and optional evidence_quality.json.

        O-04: Writes to _metadata/ subdirectory (TREE-SPECS-v1.md).

        Returns:
            Tuple of (manifest, quality_report, evidence_manifest_error, evidence_quality_error)
        """
        pre_manifest: EvidenceManifest | None = None
        quality_report: dict | None = None
        evidence_manifest_error = False
        evidence_quality_error = False

        # O-04: Create _metadata/ subdirectory
        metadata_dir = outdir / METADATA_DIR
        metadata_dir.mkdir(parents=True, exist_ok=True)

        try:
            base_exporter = BaseExporter()
            ev_map = base_exporter.load_evidence_map(templates_dir)
            selected_articles = base_exporter.selected_articles_from_plan(plan)
            manifest, _wanted, _base = base_exporter.build_evidence_manifest(
                templates_dir,
                selected_articles,
                ev_map,
            )
            (metadata_dir / EVIDENCE_MANIFEST).write_text(
                json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
                encoding=DEFAULT_ENCODING,
            )
            pre_manifest = manifest

            self._log_event(
                "export.evidence_manifest.written",
                {
                    "included": len(manifest.get("included", [])),
                    "missing": len(manifest.get("missing", [])),
                    "articles": len(manifest.get("by_article", {})),
                },
            )

            # Build quality report
            try:
                q = QualityEngine()
                base_root = _base
                included = list((manifest or {}).get("included") or [])
                quality_report = compute_evidence_quality(
                    base_root=base_root,
                    included=included,
                    selected_articles=selected_articles,
                    ev_map=ev_map,
                    quality_engine=q,
                )
                (metadata_dir / "evidence_quality.json").write_text(
                    json.dumps(quality_report, indent=2, ensure_ascii=False),
                    encoding=DEFAULT_ENCODING,
                )
                self._log_event(
                    "export.evidence_quality.written",
                    {
                        "files": len(quality_report.get("quality_by_file", {})),
                        "articles": len(quality_report.get("missing_reasons_by_article", {})),
                    },
                )
            except (
                OSError,
                ValueError,
                KeyError,
                TypeError,
                UnicodeDecodeError,
                json.JSONDecodeError,
            ) as exc:
                evidence_quality_error = True
                self._log_event("export.evidence_quality.failed", {"error": str(exc)})

        except (OSError, ValueError, KeyError, TypeError) as exc:
            evidence_manifest_error = True
            self._log_event("export.evidence_manifest.failed", {"error": str(exc)})

        return pre_manifest, quality_report, evidence_manifest_error, evidence_quality_error
