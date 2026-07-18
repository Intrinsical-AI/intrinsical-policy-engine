# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Filesystem-boundary regressions for declarative bundle exports."""

from pathlib import Path

import pytest

from intrinsical_policy_engine.adapters.export.bundles.bundle_exporter import (
    BundleExporter,
    BundlePathViolation,
)
from intrinsical_policy_engine.app.rendering.templating import ArtifactAssembler
from intrinsical_policy_engine.domain.bundles.context import EvalContext
from intrinsical_policy_engine.domain.bundles.models import BundleNode, BundleProfile
from intrinsical_policy_engine.domain.core.subject_profile import SubjectProfile


@pytest.fixture
def exporter(tmp_path: Path) -> BundleExporter:
    templates = tmp_path / "templates"
    templates.mkdir()
    (templates / "document.md.j2").write_text("safe content\n", encoding="utf-8")
    return BundleExporter(ArtifactAssembler(templates))


@pytest.fixture
def non_strict_context() -> EvalContext:
    return EvalContext(
        plan={},
        system_profile=SubjectProfile(),
        flags={"strict": False},
    )


def _profile(*nodes: BundleNode, root_dir: str = "bundle") -> BundleProfile:
    return BundleProfile(
        id="boundary-test",
        kind="test",
        root_dir=root_dir,
        nodes=list(nodes),
    )


def _file_node(name: str) -> BundleNode:
    return BundleNode(
        id="file-node",
        kind="file",
        name=name,
        template="document.md.j2",
    )


def _copy_node(*, source: str, target: str) -> BundleNode:
    return BundleNode(
        id="copy-node",
        kind="copy",
        source=source,
        target=target,
    )


@pytest.mark.parametrize("root_kind", ["traversal", "absolute"])
def test_profile_root_rejects_traversal_and_absolute_paths_in_non_strict_mode(
    exporter: BundleExporter,
    non_strict_context: EvalContext,
    tmp_path: Path,
    root_kind: str,
) -> None:
    out_root = tmp_path / "out"
    escaped_root = tmp_path / "escaped-root"
    root_dir = "../escaped-root" if root_kind == "traversal" else str(escaped_root)

    with pytest.raises(BundlePathViolation):
        exporter.export_profile(_profile(root_dir=root_dir), non_strict_context, out_root)

    assert not escaped_root.exists()


def test_profile_root_rejects_symlink_escape(
    exporter: BundleExporter,
    non_strict_context: EvalContext,
    tmp_path: Path,
) -> None:
    out_root = tmp_path / "out"
    outside = tmp_path / "outside"
    out_root.mkdir()
    outside.mkdir()
    (out_root / "linked-bundle").symlink_to(outside, target_is_directory=True)

    with pytest.raises(BundlePathViolation):
        exporter.export_profile(
            _profile(root_dir="linked-bundle"),
            non_strict_context,
            out_root,
        )


@pytest.mark.parametrize("kind", ["dir", "file", "copy"])
@pytest.mark.parametrize("destination_kind", ["traversal", "absolute"])
def test_node_destinations_cannot_escape_bundle_root(
    exporter: BundleExporter,
    non_strict_context: EvalContext,
    tmp_path: Path,
    kind: str,
    destination_kind: str,
) -> None:
    out_root = tmp_path / "out"
    source = out_root / "_metadata" / "source.txt"
    source.parent.mkdir(parents=True)
    source.write_text("private source\n", encoding="utf-8")
    outside = tmp_path / f"outside-{kind}"
    destination = f"../{outside.name}" if destination_kind == "traversal" else str(outside)

    if kind == "dir":
        node = BundleNode(id="dir-node", kind="dir", name=destination)
    elif kind == "file":
        node = _file_node(destination)
    else:
        node = _copy_node(source="_metadata/source.txt", target=destination)

    with pytest.raises(BundlePathViolation):
        exporter.export_profile(_profile(node), non_strict_context, out_root)

    assert not outside.exists()


@pytest.mark.parametrize("kind", ["dir", "file", "copy"])
def test_node_destinations_cannot_follow_symlinks_outside_bundle(
    exporter: BundleExporter,
    non_strict_context: EvalContext,
    tmp_path: Path,
    kind: str,
) -> None:
    out_root = tmp_path / "out"
    bundle_root = out_root / "bundle"
    outside = tmp_path / "outside"
    bundle_root.mkdir(parents=True)
    outside.mkdir()
    (bundle_root / "escape").symlink_to(outside, target_is_directory=True)
    source = out_root / "_metadata" / "source.txt"
    source.parent.mkdir()
    source.write_text("private source\n", encoding="utf-8")

    if kind == "dir":
        node = BundleNode(id="dir-node", kind="dir", name="escape/new-directory")
    elif kind == "file":
        node = _file_node("escape/leaked.md")
    else:
        node = _copy_node(source="_metadata/source.txt", target="escape/leaked.txt")

    with pytest.raises(BundlePathViolation):
        exporter.export_profile(_profile(node), non_strict_context, out_root)

    assert list(outside.iterdir()) == []


def test_nested_directory_cannot_move_current_directory_outside_bundle(
    exporter: BundleExporter,
    non_strict_context: EvalContext,
    tmp_path: Path,
) -> None:
    out_root = tmp_path / "out"
    nested = BundleNode(
        id="safe-dir",
        kind="dir",
        name="safe",
        children=[_file_node("../../escaped.md")],
    )

    with pytest.raises(BundlePathViolation):
        exporter.export_profile(_profile(nested), non_strict_context, out_root)

    assert not (out_root / "escaped.md").exists()


@pytest.mark.parametrize("source_kind", ["traversal", "absolute", "cwd", "missing", "directory"])
def test_copy_source_must_be_relative_existing_file_under_output_root(
    exporter: BundleExporter,
    non_strict_context: EvalContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source_kind: str,
) -> None:
    out_root = tmp_path / "out"
    out_root.mkdir()
    outside_file = tmp_path / "outside-source.txt"
    outside_file.write_text("must not be copied\n", encoding="utf-8")

    if source_kind == "traversal":
        source = "../outside-source.txt"
    elif source_kind == "absolute":
        source = str(outside_file)
    elif source_kind == "cwd":
        monkeypatch.chdir(tmp_path)
        source = outside_file.name
    elif source_kind == "missing":
        source = "_metadata/missing.txt"
    else:
        (out_root / "_metadata").mkdir()
        source = "_metadata"

    with pytest.raises(BundlePathViolation):
        exporter.export_profile(
            _profile(_copy_node(source=source, target="copied.txt")),
            non_strict_context,
            out_root,
        )

    assert not (out_root / "bundle" / "copied.txt").exists()


def test_copy_source_cannot_follow_symlink_outside_output_root(
    exporter: BundleExporter,
    non_strict_context: EvalContext,
    tmp_path: Path,
) -> None:
    out_root = tmp_path / "out"
    metadata = out_root / "_metadata"
    metadata.mkdir(parents=True)
    outside_file = tmp_path / "outside-source.txt"
    outside_file.write_text("must not be copied\n", encoding="utf-8")
    (metadata / "source.txt").symlink_to(outside_file)

    with pytest.raises(BundlePathViolation):
        exporter.export_profile(
            _profile(_copy_node(source="_metadata/source.txt", target="copied.txt")),
            non_strict_context,
            out_root,
        )

    assert not (out_root / "bundle" / "copied.txt").exists()


@pytest.mark.parametrize("missing_field", ["source", "destination"])
def test_copy_requires_both_source_and_destination(
    exporter: BundleExporter,
    non_strict_context: EvalContext,
    tmp_path: Path,
    missing_field: str,
) -> None:
    out_root = tmp_path / "out"
    source = out_root / "_metadata" / "source.txt"
    source.parent.mkdir(parents=True)
    source.write_text("safe source\n", encoding="utf-8")
    node = BundleNode(
        id="copy-node",
        kind="copy",
        source=None if missing_field == "source" else "_metadata/source.txt",
        target=None if missing_field == "destination" else "copied.txt",
    )

    with pytest.raises(BundlePathViolation):
        exporter.export_profile(_profile(node), non_strict_context, out_root)


def test_root_dot_preserves_legitimate_copy_from_metadata(
    exporter: BundleExporter,
    non_strict_context: EvalContext,
    tmp_path: Path,
) -> None:
    out_root = tmp_path / "out"
    source = out_root / "_metadata" / "summary.json"
    source.parent.mkdir(parents=True)
    source.write_text('{"status": "ok"}\n', encoding="utf-8")
    target = out_root / "report" / "summary.json"

    coverage = exporter.export_profile(
        _profile(
            _copy_node(source="_metadata/summary.json", target="report/summary.json"),
            root_dir=".",
        ),
        non_strict_context,
        out_root,
    )

    assert target.read_text(encoding="utf-8") == source.read_text(encoding="utf-8")
    assert coverage.generated_files == (target.resolve(),)
