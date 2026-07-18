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

from intrinsical_policy_engine.app.rendering.templating import ArtifactAssembler
from intrinsical_policy_engine.domain.bundles.context import EvalContext
from intrinsical_policy_engine.domain.bundles.context_builders import get_builder
from intrinsical_policy_engine.domain.bundles.models import BundleNode, BundleProfile
from intrinsical_policy_engine.domain.bundles.registry import PREDICATES

logger = logging.getLogger(__name__)


class BundlePathViolation(ValueError):
    """Raised when a bundle path escapes its declared filesystem boundary."""


def _resolve_under(root: Path, candidate: Path, *, label: str) -> Path:
    """Resolve ``candidate`` and require it to remain within ``root``.

    Resolving before any filesystem mutation also follows pre-existing symlinks,
    so a profile cannot use a symlink below the output tree to reach outside it.
    The resolved path is returned to avoid subsequently operating on the
    unvalidated, symlink-containing spelling.
    """

    try:
        resolved_root = root.resolve()
        resolved_candidate = candidate.resolve()
    except (OSError, RuntimeError) as exc:
        raise BundlePathViolation(f"{label} could not be resolved safely") from exc
    if not resolved_candidate.is_relative_to(resolved_root):
        raise BundlePathViolation(f"{label} must resolve within its output boundary")
    return resolved_candidate


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
        resolved_out_root = out_root.resolve()
        bundle_root = _resolve_under(
            resolved_out_root,
            resolved_out_root / profile.root_dir,
            label=f"Bundle profile {profile.id} root_dir",
        )
        bundle_root.mkdir(parents=True, exist_ok=True)

        # 3. Walk nodes
        return self._process_nodes(
            profile.nodes,
            context,
            bundle_root,
            bundle_root,
            resolved_out_root,
        )

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
            except BundlePathViolation as e:
                # Filesystem-boundary violations are configuration/security
                # errors. Never downgrade them in non-strict rendering mode.
                logger.error(f"Unsafe path in node {node.id}: {e}")
                raise
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
            target_path = _resolve_under(
                bundle_root,
                current_dir / str(node.name),
                label=f"Directory node {node.id} destination",
            )
            target_path.mkdir(parents=True, exist_ok=True)
            return self._process_nodes(node.children, context, target_path, bundle_root, out_root)

        elif node.kind == "file":
            if not node.template:
                logger.warning(f"File node {node.id} has no template")
                return BundleCoverage()

            target_path = _resolve_under(
                bundle_root,
                current_dir / str(node.name),
                label=f"File node {node.id} destination",
            )
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
                raise BundlePathViolation(f"Copy node {node.id} must declare a source")

            if node.target:
                raw_target = Path(node.target)
                target_path = bundle_root / raw_target
            elif node.name:
                raw_target = Path(node.name)
                target_path = current_dir / raw_target
            else:
                raise BundlePathViolation(f"Copy node {node.id} must declare a destination")

            target_path = _resolve_under(
                bundle_root,
                target_path,
                label=f"Copy node {node.id} destination",
            )
            target_path.parent.mkdir(parents=True, exist_ok=True)

            raw_src = Path(node.source)
            if raw_src.is_absolute():
                raise BundlePathViolation(
                    f"Copy node {node.id} source must be relative to the output root"
                )

            src = _resolve_under(
                out_root,
                out_root / raw_src,
                label=f"Copy node {node.id} source",
            )
            if not src.is_file():
                raise BundlePathViolation(
                    f"Copy node {node.id} source must exist as a regular file"
                )

            shutil.copy2(src, target_path)
            # Red Team Fix v2: Track copied file for manifest
            return BundleCoverage(generated_files=(target_path,))

        return BundleCoverage()
