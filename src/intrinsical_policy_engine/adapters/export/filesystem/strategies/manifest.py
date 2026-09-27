# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Manifest generation strategy: fingerprints, legal notice, and index.

Extracted from FilesystemExporter.export() lines 755-776.
Handles:
- LEGAL_NOTICE.md generation
- fingerprint.json (cryptographic integrity)
- index.json (export summary)
- ICS calendar generation (if applicable)
"""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import TemplateError
from jinja2.exceptions import TemplateNotFound

from intrinsical_policy_engine.adapters.export.filesystem.strategies.base import (
    ArtifactsDelta,
    ExportContext,
    ExportStrategy,
)
from intrinsical_policy_engine.adapters.logging import StructuredLogger
from intrinsical_policy_engine.adapters.security.gpg_signer import GpgSigner
from intrinsical_policy_engine.app.config.constants import (
    EXPORTS_DIR,
    FINGERPRINT_JSON,
    INDEX_JSON,
    METADATA_DIR,
)
from intrinsical_policy_engine.app.gating.export_gate import evaluate_bundle_coherence
from intrinsical_policy_engine.common.hashing import sha256_directory
from intrinsical_policy_engine.common.io_safety import validated_tree_files
from intrinsical_policy_engine.domain.bundles.models import BundleProfile

if TYPE_CHECKING:
    from intrinsical_policy_engine.adapters.export.filesystem.filesystem_exporter import (
        FilesystemExporter,
    )


class ManifestStrategy(ExportStrategy):
    """Generate integrity manifests and legal notices.

    This strategy runs LAST in the pipeline because:
    1. It needs all generated files for fingerprinting
    2. LEGAL_NOTICE must exist before fingerprinting
    3. index.json summarizes the entire export

    Order: LEGAL_NOTICE → index.json → actions.json → ICS → CHECKSUMS → fingerprint.json
    """

    _ROOT_ALLOWED_FILES = {
        "00_START_HERE.md",
        "LEGAL_NOTICE.md",
        "CHECKSUMS.sha256",
        "CHECKSUMS.sha256.asc",
        "CHECKSUMS.sha256.tsr",
        FINGERPRINT_JSON,
        INDEX_JSON,
        "manifest.md",
        "compliance.ics",
    }
    _ROOT_ALLOWED_DIRS = {
        "deliverables",
        "evidence",
        METADATA_DIR,
        EXPORTS_DIR,
        "tools",
    }
    _EXCLUDED_HASH_PREFIXES = (
        f"{EXPORTS_DIR}/",
        f"{METADATA_DIR}/logs/",
    )
    _EXCLUDED_PATH_REASONS = {
        f"{EXPORTS_DIR}/**": "derived artifact (zip may be non-deterministic)",
        f"{METADATA_DIR}/logs/**": "non-deterministic runtime logs",
        "**/*.lock": "ephemeral concurrency control",
    }
    _SEALED_PATHS = [
        "00_START_HERE.md",
        "LEGAL_NOTICE.md",
        INDEX_JSON,
        "manifest.md",
        "compliance.ics",
        "deliverables/**",
        "evidence/**",
        f"{METADATA_DIR}/**",
        "tools/**",
    ]

    def execute(self, exporter: FilesystemExporter, context: ExportContext) -> ArtifactsDelta:
        """Execute manifest generation pipeline.

        Args:
            exporter: Parent exporter (for file writing helpers)
            context: Shared export context (uses generated_files)

        Returns:
            ArtifactsDelta with generated manifest files.

        Raises:
            TemplateError: If strict mode and LEGAL_NOTICE template fails
            RuntimeError: If strict mode and INV-B1/B2 violations detected
        """
        generated_files: list[Path] = []

        # Fail closed on reused output trees before this strategy reads any
        # existing notice, index, checksum, or fingerprint input.
        validated_tree_files(context.out_dir)

        # 0. Validate INV-B1/B2 integrity (docs/invariants/ENGINE-ARCHITECTURE-v1.md)
        self._validate_bundle_integrity(exporter, context)

        # 1. Generate LEGAL_NOTICE.md
        legal_file = self._generate_legal_notice(exporter, context)
        if legal_file:
            generated_files.append(legal_file)

        # 2. Generate index.json
        self._generate_index(exporter, context)

        # 3. Generate actions.json (structured backlog - no Jinja, pure Python)
        actions_file = self._generate_actions_json(exporter, context)
        if actions_file:
            generated_files.append(actions_file)

        # 4. Generate ICS calendar (optional)
        ics_file = self._generate_ics_calendar(exporter, context)
        if ics_file:
            generated_files.append(ics_file)

        # 4b. Validate required root artifacts (INV-A-02)
        self._validate_required_root_files(exporter, context)

        # 5. Generate CHECKSUMS.sha256 from the final sealed file set.
        checksums_file, sig_file = self._generate_checksums(exporter, context)
        if checksums_file:
            generated_files.append(checksums_file)
        if sig_file:
            generated_files.append(sig_file)

        # 6. Generate fingerprint.json
        self._generate_fingerprint(exporter, context)

        return ArtifactsDelta.from_files(generated_files)

    def _generate_legal_notice(
        self, exporter: FilesystemExporter, context: ExportContext
    ) -> Path | None:
        """Generate LEGAL_NOTICE.md from template if exists."""
        legal_ref = "artifacts/core/LEGAL_NOTICE.md.j2"
        legal_tpl = context.templates_dir / legal_ref
        legal_out = context.out_dir / "LEGAL_NOTICE.md"

        if not legal_tpl.exists():
            if context.strict:
                raise RuntimeError("STRICT MODE: LEGAL_NOTICE.md.j2 missing")
            return None

        try:
            if not legal_out.exists():
                rendered = context.assembler.assemble(legal_ref, context.ctx)
                exporter.write_text(legal_out, rendered)

            return legal_out

        except (TemplateError, TemplateNotFound, OSError) as e:
            if context.strict:
                raise
            logger: StructuredLogger | None = getattr(exporter, "_logger", None)
            if logger:
                logger.warning("export.legal_notice.failed", {"error": str(e)})
            return None

    def _validate_required_root_files(
        self, exporter: FilesystemExporter, context: ExportContext
    ) -> None:
        """Validate required root artifacts in strict mode."""
        required = ["00_START_HERE.md", "LEGAL_NOTICE.md", "index.json"]
        missing = [name for name in required if not (context.out_dir / name).exists()]
        if not missing:
            return

        msg = f"Missing required root artifacts: {', '.join(missing)}"
        if context.strict:
            raise RuntimeError(msg)

        logger: StructuredLogger | None = getattr(exporter, "_logger", None)
        if logger:
            logger.warning("export.required_root_missing", {"missing": missing})

    def _generate_checksums(  # noqa: C901
        self, exporter: FilesystemExporter, context: ExportContext
    ) -> tuple[Path | None, Path | None]:
        """Generate CHECKSUMS.sha256 with dynamic hashing of generated files.

        Returns:
            Tuple of (checksums_path, signature_path) or (None, None) if skipped.
        """
        import hashlib

        checksums_ref = "artifacts/exports/CHECKSUMS.sha256.j2"
        checksums_tpl = context.templates_dir / checksums_ref
        checksums_out = context.out_dir / "CHECKSUMS.sha256"

        if not checksums_tpl.exists():
            if context.strict:
                raise RuntimeError("STRICT MODE: CHECKSUMS.sha256.j2 missing")
            return None, None

        # === DYNAMIC HASHING ===
        all_files: list[tuple[str, Path]] = []
        for p in validated_tree_files(context.out_dir):
            if not p.name.startswith("."):
                rel_path = p.relative_to(context.out_dir).as_posix()
                all_files.append((rel_path, p))

        excluded_filenames = {
            "CHECKSUMS.sha256",
            "CHECKSUMS.sha256.asc",
            "CHECKSUMS.sha256.tsr",
            FINGERPRINT_JSON,
        }
        hashable_files = [
            (rel_path, p)
            for rel_path, p in all_files
            if p.name not in excluded_filenames and not self._is_excluded_from_hashing(rel_path)
        ]

        file_hashes: dict[str, dict[str, str]] = {}
        for rel_path, file_path in hashable_files:
            try:
                content = file_path.read_bytes()
                file_hashes[rel_path] = {
                    "sha256": hashlib.sha256(content).hexdigest(),
                    "size": str(len(content)),
                }
            except OSError:
                file_hashes[rel_path] = {"sha256": "ERROR", "size": "0"}

        # Enforce SSOT + entrypoint invariants before writing checksums
        all_paths = [rel_path for rel_path, _ in all_files]
        self._validate_ssot_constraints(exporter, context, file_hashes, all_paths)

        answers_hash = context.ctx.get("audit", {}).get("answers_hash", "PENDING")
        answers_file = context.out_dir / METADATA_DIR / "wizard_answers.json"
        if answers_file.exists() and answers_hash in ("PENDING", "—", None):
            with contextlib.suppress(OSError):
                answers_hash = hashlib.sha256(answers_file.read_bytes()).hexdigest()

        if "audit" not in context.ctx:
            context.ctx["audit"] = {}

        context.ctx["audit"]["files"] = file_hashes
        context.ctx["audit"]["answers_hash"] = answers_hash
        context.ctx["audit"]["total_files"] = len(file_hashes)

        signer = GpgSigner()
        context.ctx["gpg_signature_available"] = signer.has_secret_key()

        # Signing policy is explicit and supplied by the composition root.
        skip_gpg = bool(context.config.get("skip_gpg_signing", False))
        logger: StructuredLogger | None = getattr(exporter, "_logger", None)

        try:
            rendered = context.assembler.assemble(checksums_ref, context.ctx)
            exporter.write_text(checksums_out, rendered)

            sig_path: Path | None = None
            if skip_gpg:
                # GPG signing explicitly skipped via environment variable
                if logger:
                    logger.info(
                        "export.gpg.skipped",
                        {"reason": "explicit skip_gpg_signing policy"},
                    )
            elif signer.is_available():
                if signer.has_secret_key():
                    sig_path = signer.sign_file(checksums_out)
                    if sig_path is None or not sig_path.is_file():
                        message = "GPG signing failed: signature file was not created"
                        if context.strict or bool(context.config.get("release", False)):
                            raise RuntimeError(message)
                        if logger:
                            logger.warning(
                                "export.gpg.signing_failed",
                                {"path": str(checksums_out)},
                            )
                        sig_path = None
                elif context.strict or bool(context.config.get("release", False)):
                    raise RuntimeError("STRICT MODE: GPG Secret Key required but not found.")
            elif context.strict or bool(context.config.get("release", False)):
                raise RuntimeError("STRICT MODE: GPG binary required but not available.")

            return checksums_out, sig_path

        except (TemplateError, TemplateNotFound, OSError) as e:
            if context.strict:
                raise
            if not logger:
                logger = getattr(exporter, "_logger", None)
            if logger:
                logger.warning("export.checksums.failed", {"error": str(e)})
            return None, None

    def _validate_ssot_constraints(
        self,
        exporter: FilesystemExporter,
        context: ExportContext,
        file_hashes: dict[str, dict[str, str]],
        all_paths: list[str],
    ) -> None:
        """Enforce SSOT and single-entrypoint invariants."""
        violations: list[str] = []

        # INV-PKG-01: JSONL only allowed in _metadata/trace.jsonl or _metadata/logs/
        invalid_jsonl = [
            path
            for path in all_paths
            if path.endswith(".jsonl")
            and path != f"{METADATA_DIR}/trace.jsonl"
            and not path.startswith(f"{METADATA_DIR}/logs/")
        ]
        if invalid_jsonl:
            violations.append(
                "JSONL files outside _metadata/logs/: " + ", ".join(sorted(invalid_jsonl))
            )

        # INV-PKG-02: root must be a strict whitelist
        root_entries = {path.split("/", 1)[0] for path in all_paths}
        allowed = self._ROOT_ALLOWED_FILES | self._ROOT_ALLOWED_DIRS
        extra_roots = sorted(entry for entry in root_entries if entry not in allowed)
        if extra_roots:
            violations.append("Unexpected root entries: " + ", ".join(extra_roots))

        # INV-UX-02: no 00_START_HERE.md outside root
        extra_start_here = [
            path
            for path in file_hashes
            if path.endswith("00_START_HERE.md") and path != "00_START_HERE.md"
        ]
        if extra_start_here:
            violations.append(
                "Found 00_START_HERE.md outside root: " + ", ".join(sorted(extra_start_here))
            )

        # INV-SSOT-03: no evidence file duplication in deliverables/
        evidence_hashes = {
            info.get("sha256")
            for path, info in file_hashes.items()
            if path.startswith("evidence/") and info.get("sha256") not in (None, "ERROR")
        }
        duplicate_in_deliverables = [
            path
            for path, info in file_hashes.items()
            if path.startswith("deliverables/") and info.get("sha256") in evidence_hashes
        ]
        if duplicate_in_deliverables:
            violations.append(
                "Evidence duplicated under deliverables/: "
                + ", ".join(sorted(duplicate_in_deliverables))
            )

        # INV-DELIV-01: Article views in audit_deep must use *_View naming
        audit_paths = [path for path in all_paths if path.startswith("deliverables/audit_deep/")]
        bad_view_names = [
            path for path in audit_paths if "/TOPIC-" in path and "evidence" in path.lower()
        ]
        if bad_view_names:
            violations.append(
                "Audit deep article views must use *_View naming: "
                + ", ".join(sorted(bad_view_names))
            )

        if not violations:
            return

        msg = "SSOT/entrypoint violations: " + " | ".join(violations)
        if context.strict:
            raise RuntimeError(msg)
        logger: StructuredLogger | None = getattr(exporter, "_logger", None)
        if logger:
            logger.warning("export.ssot.violations", {"details": violations})

    def _generate_fingerprint(self, exporter: FilesystemExporter, context: ExportContext) -> None:
        """Generate fingerprint.json from all generated files."""
        files_to_hash: list[Path] = []

        # The validated scan already includes every generated file under the
        # output root. Do not trust generated_files paths independently: stale
        # or external paths must never become fingerprint inputs.
        for p in validated_tree_files(context.out_dir):
            if p.name == FINGERPRINT_JSON or p.name.startswith("."):
                continue
            rel_path = p.relative_to(context.out_dir).as_posix()
            if self._is_excluded_from_hashing(rel_path):
                continue
            files_to_hash.append(p.resolve(strict=True))

        # Skip fingerprint if no files to hash (e.g., minimal test bundles)
        if not files_to_hash:
            logger: StructuredLogger | None = getattr(exporter, "_logger", None)
            if logger:
                logger.warning("export.fingerprint.skipped", {"reason": "no_files"})
            return

        fingerprint = exporter.make_fingerprint(files_to_hash)
        exporter.write_json(context.out_dir / FINGERPRINT_JSON, fingerprint)

    def _generate_index(self, exporter: FilesystemExporter, context: ExportContext) -> None:
        """Generate index.json with export summary."""
        summary = exporter.evidence_summary(context.out_dir)
        plan = context.plan or {}
        system_profile = plan.get("system_profile") or {}
        outcome_axes = plan.get("outcome_axes") or {}
        deliverables_dir = context.out_dir / "deliverables"
        deliverable_views = (
            sorted(p.name for p in deliverables_dir.iterdir() if p.is_dir())
            if deliverables_dir.exists()
            else []
        )
        evidence_root = context.out_dir / "evidence"
        evidence_root_hash = sha256_directory(evidence_root, warn_if_missing=False)
        exporter.write_json(
            context.out_dir / INDEX_JSON,
            {
                "target": "filesystem",
                "tree_spec_version": "v1",
                "roles": system_profile.get("roles", []),
                "risk_class": outcome_axes.get("risk_tier") or plan.get("outcome"),
                "deliverables": deliverable_views,
                "evidence_root_hash": evidence_root_hash,
                "sealed_paths": list(self._SEALED_PATHS),
                "excluded_paths": sorted(self._EXCLUDED_PATH_REASONS.keys()),
                "reason_excluded": dict(self._EXCLUDED_PATH_REASONS),
                **summary,
            },
        )

    def _relative_to_out_dir(self, context: ExportContext, path: Path) -> str | None:
        try:
            return path.relative_to(context.out_dir.resolve()).as_posix()
        except ValueError:
            return None

    def _is_excluded_from_hashing(self, rel_path: str) -> bool:
        return rel_path.endswith(".lock") or any(
            rel_path.startswith(prefix) for prefix in self._EXCLUDED_HASH_PREFIXES
        )

    def _generate_ics_calendar(
        self, exporter: FilesystemExporter, context: ExportContext
    ) -> Path | None:
        """Generate ICS calendar file if due dates are present."""
        try:
            due_hints = context.plan.get("due_hints", {})
            if not due_hints:
                return None

            ics_path = exporter.export_ics(context.out_dir, context.plan)
            if ics_path and ics_path.exists():
                return ics_path
            return None

        except Exception as e:
            if context.strict:
                raise
            logger: StructuredLogger | None = getattr(exporter, "_logger", None)
            if logger:
                logger.warning("export.ics.failed", {"error": str(e)})
            return None

    def _validate_bundle_integrity(
        self, exporter: FilesystemExporter, context: ExportContext
    ) -> None:
        """Validate INV-B1 (coverage) and INV-B2 (no phantom references).

        Per docs/invariants/ENGINE-ARCHITECTURE-v1.md and TEST DRIVEN DEVELOPMENT v1:
        - INV-B1: Critical actions must be covered by bundle nodes
        - INV-B2: trace_back_to references must exist in ContractBundle

        In strict mode, violations cause export to fail (FAIL HARD).
        In non-strict mode, warnings are logged but export continues.
        """
        logger: StructuredLogger | None = getattr(exporter, "_logger", None)
        bundle_profiles = context.config.get("bundle_profiles") or {}
        contract_bundle = context.config.get("bundle")
        export_mode = str(context.config.get("export_mode", "full"))
        profiles_dict = {
            pid: p for pid, p in bundle_profiles.items() if isinstance(p, BundleProfile)
        }

        coherence = evaluate_bundle_coherence(
            bundle_profiles,
            contract_bundle,
            context.plan,
            strict=context.strict,
            export_mode=export_mode,
        )

        if coherence.blocked_reason == "config" and coherence.config_warning:
            if logger:
                logger.warning(
                    "bundle.validation.partial_config", {"reason": coherence.config_warning}
                )
            if context.strict:
                raise RuntimeError(f"STRICT MODE: {coherence.config_warning}")
            return

        if coherence.integrity_report and coherence.integrity_report.has_errors():
            error_msg = (
                f"[INV-B2] Phantom references detected: {coherence.integrity_report.summary()}"
            )
            if logger:
                logger.error(
                    "bundle.integrity.failed", {"problems": coherence.integrity_report.problems}
                )

            if context.strict and coherence.blocked_reason == "integrity":
                raise RuntimeError(error_msg)

        if coherence.coverage_report and coherence.coverage_report.has_critical_gaps():
            error_msg = f"[INV-B1] Coverage gaps detected: {coherence.coverage_report.summary()}"
            if logger:
                logger.error(
                    "bundle.coverage.failed",
                    {
                        "missing_actions": list(coherence.coverage_report.missing_actions),
                        "missing_evidences": list(coherence.coverage_report.missing_evidences),
                        "active_profiles": coherence.coverage_report.active_profiles,
                    },
                )

            if context.strict and coherence.blocked_reason == "coverage":
                raise RuntimeError(error_msg)

        if logger and coherence.coverage_report:
            logger.info(
                "bundle.validation.passed",
                {
                    "profiles_validated": len(profiles_dict),
                    "active_profiles": coherence.coverage_report.active_profiles,
                    "covered_actions": len(coherence.coverage_report.covered_actions),
                },
            )

    def _generate_actions_json(
        self, exporter: FilesystemExporter, context: ExportContext
    ) -> Path | None:
        """Generate actions.json directly in Python (no Jinja)."""
        import json

        from intrinsical_policy_engine.domain.bundles.view_models import build_backlog_views

        metadata_dir = context.out_dir / METADATA_DIR
        metadata_dir.mkdir(parents=True, exist_ok=True)
        actions_out = metadata_dir / "actions.json"

        actions_meta = context.plan.get("actions_meta", []) or []
        due_hints = context.plan.get("due_hints", {}) or {}
        meta = context.ctx.get("meta", {}) or {}
        generated_at = meta.get("generated_at")
        base_date = str(generated_at)[:10] if isinstance(generated_at, str) and generated_at else ""

        backlog = build_backlog_views(
            actions_meta=actions_meta,
            due_hints=due_hints,
            base_date=base_date,
        )

        def action_to_dict(a):
            return {
                "id": a.id,
                "title": a.title,
                "priority": a.priority,
                "articles": a.articles.split(";") if a.articles else [],
            }

        actions_data = {
            "schema_version": "1.0",
            "generated_at": meta.get("generated_at", ""),
            "total": len(backlog.all),
            "by_role": {
                "engineering": [action_to_dict(a) for a in backlog.engineering],
                "legal": [action_to_dict(a) for a in backlog.legal],
                "compliance": [action_to_dict(a) for a in backlog.compliance],
                "governance": [action_to_dict(a) for a in backlog.governance],
            },
            "by_priority": {
                "critical": list(backlog.by_priority.get("critical", [])),
                "high": list(backlog.by_priority.get("high", [])),
                "medium": list(backlog.by_priority.get("medium", [])),
                "low": list(backlog.by_priority.get("low", [])),
            },
        }

        try:
            with open(actions_out, "w", encoding="utf-8") as f:
                json.dump(actions_data, f, indent=2, ensure_ascii=False)
            return actions_out

        except OSError as e:
            if context.strict:
                raise
            logger: StructuredLogger | None = getattr(exporter, "_logger", None)
            if logger:
                logger.warning("export.actions_json.failed", {"error": str(e)})
            return None
