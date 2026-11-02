# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Render selected profile nodes from a captured pack snapshot."""

from pathlib import Path

from intrinsical_policy_engine.app.rendering.templating import ArtifactAssembler
from intrinsical_policy_engine.domain.bundles.context import EvalContext
from intrinsical_policy_engine.domain.bundles.context_builders import get_builder
from intrinsical_policy_engine.domain.bundles.models import BundleProfile
from intrinsical_policy_engine.domain.bundles.selection import selected_nodes


class BundleExporter:
    """Write selected nodes; coverage is computed from the same selection elsewhere."""

    def __init__(self, assembler: ArtifactAssembler):
        self._assembler = assembler

    def export_profile(self, profile: BundleProfile, context: EvalContext, out_root: Path) -> None:
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
