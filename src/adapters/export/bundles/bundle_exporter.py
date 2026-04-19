# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Bundle exporter: renders BundleProfiles to disk.

Executes declarative bundle profiles (YAML) by walking their node tree
and rendering templates to the output directory. Returns coverage metrics
for quality gating and traceability.
"""

import logging
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from src.app.rendering.templating import ArtifactAssembler
from src.domain.bundles.context import EvalContext
from src.domain.bundles.context_builders import get_builder
from src.domain.bundles.models import BundleNode, BundleProfile
from src.domain.bundles.registry import PREDICATES

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class BundleCoverage:
    """Coverage metrics returned by the bundle exporter."""

    covered_actions: set[str] = field(default_factory=set)
    covered_evidences: set[str] = field(default_factory=set)
    # Red Team Fix v2: Track generated files for manifest hashing
    generated_files: tuple[Path, ...] = field(default_factory=tuple)

    def __add__(self, other: "BundleCoverage") -> "BundleCoverage":
        return BundleCoverage(
            covered_actions=self.covered_actions | other.covered_actions,
            covered_evidences=self.covered_evidences | other.covered_evidences,
            generated_files=self.generated_files + other.generated_files,
        )


class BundleExporter:
    """Exports a BundleProfile to disk and returns coverage data."""

    def __init__(self, assembler: ArtifactAssembler):
        self._assembler = assembler

    def export_profile(
        self, profile: BundleProfile, context: EvalContext, out_root: Path
    ) -> BundleCoverage:
        """Export a full profile if it applies."""

        # 1. Check applies_if
        if not PREDICATES.evaluate_all(profile.applies_if, context):
            logger.debug(f"Skipping profile {profile.id} (conditions not met)")
            return BundleCoverage()

        logger.info(f"Exporting bundle profile: {profile.id}")

        # 2. Prepare root directory
        bundle_root = out_root / profile.root_dir
        bundle_root.mkdir(parents=True, exist_ok=True)

        # 3. Walk nodes
        return self._process_nodes(profile.nodes, context, bundle_root, bundle_root, out_root)

    def _process_nodes(
        self,
        nodes: list[BundleNode],
        context: EvalContext,
        current_dir: Path,
        bundle_root: Path,
        out_root: Path,
    ) -> BundleCoverage:
        """Recursively process a list of nodes."""
        coverage = BundleCoverage()

        for node in nodes:
            # Check node predicates
            if not PREDICATES.evaluate_all(node.predicates, context):
                continue

            # Collect coverage from this node
            if node.trace_back_to:
                raw_actions = node.trace_back_to.get("actions")
                raw_evidences = node.trace_back_to.get("evidences")

                action_ids: set[str] = set()
                if isinstance(raw_actions, list):
                    action_ids = {str(action) for action in raw_actions if isinstance(action, str)}

                evidence_ids: set[str] = set()
                if isinstance(raw_evidences, list):
                    evidence_ids = {
                        str(evidence) for evidence in raw_evidences if isinstance(evidence, str)
                    }

                coverage = coverage + BundleCoverage(
                    covered_actions=action_ids,
                    covered_evidences=evidence_ids,
                )

            # Execute node
            try:
                child_coverage = self._execute_node(
                    node, context, current_dir, bundle_root, out_root
                )
                coverage = coverage + child_coverage
            except Exception as e:
                logger.error(f"Failed to process node {node.id}: {e}")
                if context.flags.get("strict", False):
                    raise

        return coverage

    def _execute_node(
        self,
        node: BundleNode,
        context: EvalContext,
        current_dir: Path,
        bundle_root: Path,
        out_root: Path,
    ) -> BundleCoverage:
        """Execute a single node (file/dir/copy)."""

        if node.kind in {"dir", "file"} and node.name is None:
            logger.warning(f"{node.kind.title()} node {node.id} has no name")
            return BundleCoverage()

        if node.kind == "dir":
            target_path = current_dir / str(node.name)
            target_path.mkdir(parents=True, exist_ok=True)
            return self._process_nodes(node.children, context, target_path, bundle_root, out_root)

        elif node.kind == "file":
            if not node.template:
                logger.warning(f"File node {node.id} has no template")
                return BundleCoverage()

            target_path = current_dir / str(node.name)
            target_path.parent.mkdir(parents=True, exist_ok=True)

            builder = get_builder(node.context)
            tpl_ctx = builder(context)

            # FIX 2025-12-27: Inject node context string into template variables
            # This allows generic templates to know their identity (e.g. "TOPIC-6")
            # even if they use the 'default' builder.
            if not tpl_ctx.get("context"):
                tpl_ctx["context"] = node.context
            # Expose predicates so view templates can show "applies if" conditions.
            tpl_ctx["predicates"] = list(node.predicates or [])

            try:
                content = self._assembler.assemble(node.template, tpl_ctx)
                target_path.write_text(content, encoding="utf-8")
                # Red Team Fix v2: Track generated file for manifest
                return BundleCoverage(generated_files=(target_path,))
            except Exception as e:
                logger.error(f"Template error in {node.id} ({node.template}): {e}")
                raise

        elif node.kind == "copy":
            if not node.source:
                logger.warning(f"Copy node {node.id} has no source")
                return BundleCoverage()

            if node.target:
                target_path = bundle_root / node.target
            elif node.name:
                target_path = current_dir / node.name
            else:
                logger.warning(f"Copy node {node.id} has no target or name")
                return BundleCoverage()

            target_path.parent.mkdir(parents=True, exist_ok=True)

            raw_src = Path(node.source)
            candidates = (
                (raw_src,)
                if raw_src.is_absolute()
                else (
                    out_root / node.source,
                    raw_src,
                )
            )
            src = next((p for p in candidates if p.exists()), None)
            if src is None:
                logger.warning(f"Copy source not found: {node.source}")
                return BundleCoverage()

            shutil.copy2(src, target_path)
            # Red Team Fix v2: Track copied file for manifest
            return BundleCoverage(generated_files=(target_path,))

        return BundleCoverage()
