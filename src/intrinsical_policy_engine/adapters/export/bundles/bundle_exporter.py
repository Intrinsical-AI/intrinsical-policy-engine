# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Render selected profile nodes from a captured pack snapshot."""

from dataclasses import dataclass, field
from pathlib import Path

from intrinsical_policy_engine.app.rendering.templating import ArtifactAssembler
from intrinsical_policy_engine.domain.bundles.context import EvalContext
from intrinsical_policy_engine.domain.bundles.context_builders import get_builder
from intrinsical_policy_engine.domain.bundles.models import BundleProfile
from intrinsical_policy_engine.domain.bundles.selection import selected_nodes


@dataclass(frozen=True)
class BundleCoverage:
    covered_actions: set[str] = field(default_factory=set)
    covered_evidences: set[str] = field(default_factory=set)
    generated_files: tuple[Path, ...] = ()


class BundleExporter:
    def __init__(self, assembler: ArtifactAssembler):
        self._assembler = assembler

    def export_profile(
        self, profile: BundleProfile, context: EvalContext, out_root: Path
    ) -> BundleCoverage:
        actions: set[str] = set()
        evidences: set[str] = set()
        generated: list[Path] = []
        source_bytes = context.extras.get("source_bytes", {})
        for node, relative in selected_nodes(profile, set(context.plan.get("flags", []))):
            path = out_root / relative
            if not path.resolve().is_relative_to(out_root.resolve()):
                raise ValueError(f"Output escapes staging directory: {relative}")
            if node.kind == "dir":
                path.mkdir(parents=True, exist_ok=True)
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            if node.kind == "file":
                if node.template is None:
                    raise ValueError(f"Missing template for {node.id}")
                rendered = self._assembler.render(node.template, get_builder(node.context)(context))
                with path.open("x", encoding="utf-8") as stream:
                    stream.write(rendered)
            else:
                if node.source not in source_bytes:
                    raise ValueError(f"Copy source absent from compiled snapshot: {node.source}")
                with path.open("xb") as stream:
                    stream.write(source_bytes[node.source])
            generated.append(path)
            if node.counts_for_coverage:
                actions.update(node.trace_back_to.actions)
                evidences.update(node.trace_back_to.evidences)
        return BundleCoverage(actions, evidences, tuple(generated))
