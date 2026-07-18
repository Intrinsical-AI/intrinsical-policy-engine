# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Omissions report generation strategy.

Generates 08_OMISSIONS_REPORT.md which explicitly lists what was NOT evaluated,
adhering to the "No Surprise Gaps" product guarantee.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from intrinsical_policy_engine.adapters.export.filesystem.strategies.base import (
    ArtifactsDelta,
    ExportStrategy,
)

if TYPE_CHECKING:
    from intrinsical_policy_engine.adapters.export.filesystem.filesystem_exporter import (
        FilesystemExporter,
    )
    from intrinsical_policy_engine.adapters.export.filesystem.strategies.base import ExportContext


class OmissionsStrategy(ExportStrategy):
    """Generates the mandatory 08_OMISSIONS_REPORT.md.

    This report explicitly lists what was NOT evaluated, adhering to the
    "No Surprise Gaps" product guarantee. It covers:
    - Omitted Roles (e.g. Importer, Distributor)
    - Omitted Scopes (e.g. low risk articles)
    - Omitted PII (Sanitization Policy)
    """

    def execute(self, exporter: FilesystemExporter, context: ExportContext) -> ArtifactsDelta:
        """Generate the omissions report."""
        legacy_root_path = context.out_dir / "08_OMISSIONS_REPORT.md"
        if legacy_root_path.exists():
            legacy_root_path.unlink()

        # SSOT: omissions live in the public snapshot view, not at root.
        out_path = (
            context.out_dir / "deliverables" / "public_snapshot_v1" / "08_OMISSIONS_REPORT.md"
        )
        out_path.parent.mkdir(parents=True, exist_ok=True)

        # 1. Gather Data
        plan = context.plan
        system_profile = plan.get("system_profile", {})
        active_roles = system_profile.get("roles", [])

        # Try to infer all available roles from bundle questions/flags if possible
        # For v1, we hardcode the known major roles for comparison.
        known_roles = {
            "provider",
            "deployer",
            "importer",
            "distributor",
            "product_manufacturer",
            "authorized_representative",
        }
        omitted_roles = sorted(list(known_roles - set(active_roles)))

        # Check wizard answers for completion status (heuristic)
        _ = context.ctx.get("wizard_answers", {})  # Reserved for future use

        # 2. Build Report Content
        lines = [
            "# Omissions Report",
            "",
            "OmittedBy: roles, policy, scope",
            "",
            (
                "> **Invariant**: This document lists explicitly what was excluded "
                "from the assessment."
            ),
            "> Absence of evidence here does NOT imply compliance.",
            "",
            "## 1. Omitted By Role",
            "The following roles were **NOT** evaluated in this snapshot:",
            "",
        ]

        if omitted_roles:
            for role in omitted_roles:
                lines.append(f"- **{role}**")
        else:
            lines.append("- *(None - All major roles evaluated)*")

        lines.extend(
            [
                "",
                "**Impact**: Obligations specific to these roles are not present in the backlog.",
                "",
                "## 2. Omitted By Policy (Sanitization)",
                (
                    "To ensure this bundle is safe for public distribution, "
                    "the following data is strictly excluded:"
                ),
                "",
                (
                    "- **PII**: Real names, emails, phone numbers "
                    "(replaced by stubs or sanitization tags)."
                ),
                "- **Secrets**: API keys, internal hostnames, private tokens.",
                "- **Proprietary Data**: Training datasets, rigid trade secrets.",
                "",
                "## 3. Scope Limitations",
                "This snapshot assumes the system classification derived from inputs.",
                f"- **System Risk Tier**: {plan.get('outcome', 'Unknown')}",
                "",
                "If the system classification changes (e.g. from Limited Risk to Review),",
                "massive sets of obligations currently omitted will become applicable.",
            ]
        )

        # 3. Write File
        content = "\n".join(lines)
        out_path.write_text(content, encoding="utf-8")

        # 4. Return delta with generated file
        return ArtifactsDelta.from_files([out_path])
