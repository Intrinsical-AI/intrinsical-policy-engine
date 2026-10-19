# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""The single renderer for compiled profiles and authored pack documents."""

import csv
from pathlib import Path
from typing import Any

from intrinsical_policy_engine.adapters.export.base.exporters.base_exporter import BaseExporter
from intrinsical_policy_engine.adapters.export.bundles.bundle_exporter import BundleExporter
from intrinsical_policy_engine.app.config.context import build_artifact_context
from intrinsical_policy_engine.app.export.artifacts import write_json
from intrinsical_policy_engine.app.rendering.templating import ArtifactAssembler
from intrinsical_policy_engine.domain.bundles.backlog_config import split_actions
from intrinsical_policy_engine.domain.bundles.context import EvalContext
from intrinsical_policy_engine.domain.services.regulatory_meta import build_regulatory_warnings


class FilesystemExporter(BaseExporter):
    def export(self, plan: dict[str, Any], templates_dir: str, out_dir: str) -> None:
        cfg = self._config or {}
        pack = cfg["compiled_pack"]
        root = Path(out_dir)
        sources = {
            Path(name)
            .relative_to(pack.layout.templates_dir.relative_to(pack.layout.framework_dir))
            .as_posix(): data
            for name, data in pack.source_bytes.items()
            if (pack.layout.framework_dir / name).is_relative_to(pack.layout.templates_dir)
        }
        assembler = ArtifactAssembler(Path(templates_dir), template_sources=sources)
        context = build_artifact_context(plan, defaults=pack.context_defaults)
        context["backlog_config"] = pack.backlog_config
        context["source_bytes"] = pack.source_bytes
        context["evidence_map"] = {
            key: [entry.model_dump() for entry in values]
            for key, values in pack.bundle.evidence_map.items()
        }
        context["framework"] = {
            "id": pack.bundle.manifest.framework_id,
            "version": pack.bundle.manifest.version,
            "name": pack.bundle.runtime.presentation.framework_name,
        }
        regulatory = pack.bundle.rules.regulatory_meta
        context["regulatory_meta"] = None
        context["regulatory_warnings"] = []
        if regulatory is not None:
            context["regulatory_meta"] = regulatory.model_dump(mode="json")
            context["regulatory_warnings"] = list(build_regulatory_warnings(regulatory).warnings)
        evaluation = EvalContext(plan=plan, extras=context)
        renderer = BundleExporter(assembler)
        for profile in cfg["profiles"].values():
            renderer.export_profile(profile, evaluation, root)
        for template, output in (
            ("artifacts/core/README.md.j2", "README.md"),
            ("artifacts/core/LEGAL_NOTICE.md.j2", "LEGAL_NOTICE.md"),
        ):
            if template in sources:
                destination = root / output
                if not destination.exists():
                    with destination.open("x", encoding="utf-8") as stream:
                        stream.write(assembler.render(template, context))
        write_json(root / "_metadata" / "actions.json", plan.get("actions_meta", []))
        grouped = split_actions(plan.get("actions_meta", []), pack.backlog_config)
        for split in pack.backlog_config.splits:
            path = root / "backlogs" / split.filename
            if not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f"Unsafe backlog filename: {split.filename}")
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("x", encoding="utf-8", newline="") as stream:
                writer = csv.writer(stream)
                writer.writerow(["Action ID", "Title", "Priority", "Due date"])
                for action in grouped[split.id]:
                    writer.writerow(
                        [
                            action["id"],
                            action["title"],
                            action["priority"],
                            plan.get("due_hints", {}).get(action["id"], ""),
                        ]
                    )
        self.export_ics(plan, root, prodid=pack.bundle.runtime.presentation.ics_prodid)
