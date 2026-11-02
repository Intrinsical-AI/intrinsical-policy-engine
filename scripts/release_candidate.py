# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Bind one distribution pair to its source and installed acceptance matrix.

Preparing or verifying a candidate never publishes it or records human acceptance.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import stat
import subprocess
import sys
import tarfile
import tomllib
import xml.etree.ElementTree as ET
import zipfile
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING, Any

from intrinsical_policy_engine.common.document_io import parse_document

if TYPE_CHECKING or __package__:
    from . import check_conformance, check_documented_examples, check_installed_artifact
else:
    # Direct execution also works when safe-path mode omits the script directory.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import check_conformance
    import check_documented_examples
    import check_installed_artifact

ROOT = Path(__file__).resolve().parents[1]
PYTHONS = ("3.11", "3.12")
PROJECT = "intrinsical_policy_engine"
DELIVERY_RECORDS = ("docs/delivery-plan.json", "docs/implementation-status.json")


class CandidateError(ValueError):
    """Candidate inputs or acceptance receipts disagree."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise CandidateError(message)


def object_value(value: object, message: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise CandidateError(message)
    return value


def text_value(value: object, message: str) -> str:
    if not isinstance(value, str) or not value:
        raise CandidateError(message)
    return value


def sha256(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def file_info(path: Path) -> dict[str, Any]:
    require(path.is_file() and not path.is_symlink(), f"Not a regular input: {path.name}")
    content = path.read_bytes()
    return {"name": path.name, "sha256": sha256(content), "size": len(content)}


def read_object(path: Path) -> dict[str, Any]:
    file_info(path)
    try:
        value = parse_document(path.read_text(encoding="utf-8"), ".json")
    except (UnicodeError, ValueError) as error:
        raise CandidateError(f"Invalid JSON receipt: {path.name}") from error
    return object_value(value, f"Receipt must be an object: {path.name}")


def source_state(root: Path) -> dict[str, Any]:
    def git(*arguments: str) -> bytes:
        environment = {
            key: value for key, value in os.environ.items() if not key.startswith("GIT_")
        }
        return subprocess.check_output(
            ["git", "--no-optional-locks", "-C", str(root), *arguments],
            timeout=30,
            env=environment,
        )

    revision = git("rev-parse", "HEAD").decode("ascii").strip()
    status = git("status", "--porcelain=v1", "--untracked-files=all", "-z")
    names = git("ls-files", "-z", "--cached", "--others", "--exclude-standard").split(b"\0")
    files: dict[str, str] = {}
    for encoded in sorted(set(names) - {b""}):
        relative = os.fsdecode(encoded)
        if relative in DELIVERY_RECORDS:
            continue
        path = root / relative
        if path.exists() or path.is_symlink():
            file_info(path)
            files[relative] = sha256(path.read_bytes())
    canonical = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {
        "revision": revision,
        "dirty": bool(status),
        "git_status_sha256": sha256(status),
        "files_sha256": sha256(canonical),
        "files": files,
        "excluded_mutable_delivery_records": list(DELIVERY_RECORDS),
    }


def project_version(root: Path) -> str:
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    require(project["name"] == "intrinsical-policy-engine", "Unexpected project name")
    return text_value(project["version"], "Missing project version")


def distribution_inventory(directory: Path, root: Path) -> list[dict[str, Any]]:
    version = project_version(root)
    wheel_name = f"{PROJECT}-{version}-py3-none-any.whl"
    sdist_name = f"{PROJECT}-{version}.tar.gz"
    paths = sorted(directory.glob("*.whl")) + sorted(directory.glob("*.tar.gz"))
    require(
        {path.name for path in paths} == {wheel_name, sdist_name},
        "Expected one current wheel and one source distribution",
    )
    expected = {
        path.relative_to(root / "src").as_posix(): sha256(path.read_bytes())
        for path in (root / "src" / PROJECT).rglob("*.py")
    }
    require(bool(expected), "Missing engine sources")
    marker = root / "src" / PROJECT / "py.typed"
    file_info(marker)
    expected[f"{PROJECT}/py.typed"] = sha256(marker.read_bytes())
    result = []
    for path in paths:
        info = file_info(path)
        if path.name == wheel_name:
            with zipfile.ZipFile(path) as archive:
                names = archive.namelist()
                require(len(names) == len(set(names)), "Duplicate wheel members")
                marker_name = f"{PROJECT}/py.typed"
                if marker_name in names:
                    require(
                        not stat.S_ISLNK(archive.getinfo(marker_name).external_attr >> 16),
                        "Linked py.typed marker in wheel",
                    )
                actual = {
                    name: sha256(archive.read(name))
                    for name in names
                    if name.endswith(".py") or PurePosixPath(name).name == "py.typed"
                }
        else:
            with tarfile.open(path) as archive:
                actual = _sdist_sources(archive, root)
        require(actual == expected, f"Distribution sources differ from checkout: {path.name}")
        result.append(info)
    return result


def _sdist_sources(archive: tarfile.TarFile, root: Path) -> dict[str, str]:
    actual: dict[str, str] = {}
    seen: set[str] = set()
    metadata: set[str] = set()
    for member in archive.getmembers():
        require(member.name not in seen, "Duplicate source distribution members")
        seen.add(member.name)
        parts = PurePosixPath(member.name).parts
        require(not member.issym() and not member.islnk(), "Links in source distribution")
        if (
            member.isfile()
            and len(parts) > 2
            and parts[1] == "src"
            and (member.name.endswith(".py") or parts[-1] == "py.typed")
        ):
            stream = archive.extractfile(member)
            if stream is None:
                raise CandidateError("Missing source distribution member bytes")
            actual[PurePosixPath(*parts[2:]).as_posix()] = sha256(stream.read())
        if member.isfile() and len(parts) == 2 and parts[1] in {"pyproject.toml", "README.md"}:
            stream = archive.extractfile(member)
            if stream is None:
                raise CandidateError("Missing source metadata bytes")
            require(
                stream.read() == (root / parts[1]).read_bytes(),
                f"Stale source metadata: {parts[1]}",
            )
            metadata.add(parts[1])
    require(metadata == {"pyproject.toml", "README.md"}, "Missing source distribution metadata")
    return actual


def prepare(directory: Path, root: Path) -> dict[str, Any]:
    before = source_state(root)
    result = {
        "schema": "ipe.release-inputs/1",
        "version": project_version(root),
        "source": before,
        "artifacts": distribution_inventory(directory, root),
        "runtime_requirements": file_info(directory / "runtime-requirements.txt"),
    }
    require(source_state(root) == before, "Sources changed while preparing release inputs")
    return result


def _require_receipt_identity(value: dict[str, Any], schema: str, python: str) -> None:
    require(value.get("schema") == schema, f"Unsupported receipt: {schema}")
    require(
        type(value.get("schema_version")) is int and value["schema_version"] == 1,
        "Unsupported receipt schema version",
    )
    require(value.get("status") == "passed", f"Acceptance failed: {schema}")
    interpreter = object_value(value.get("python"), "Missing interpreter provenance")
    version = interpreter.get("version")
    require(
        isinstance(version, str) and version.startswith(python + "."),
        "Wrong acceptance interpreter",
    )


def _require_artifact(value: dict[str, Any], artifact: dict[str, Any]) -> None:
    require(value.get("artifact_verified_unchanged") is True, "Artifact was not verified unchanged")
    observed = object_value(value.get("artifact"), "Missing artifact identity")
    require(
        observed.get("sha256") == artifact["sha256"] and observed.get("size") == artifact["size"],
        "Receipt belongs to another artifact",
    )
    require(
        Path(text_value(observed.get("path"), "Missing artifact path")).name == artifact["name"],
        "Wrong receipt artifact name",
    )


def _require_fixture(value: dict[str, Any], expected: dict[str, Any]) -> None:
    fixture = object_value(value.get("fixtures"), "Missing fixture identity")
    require(
        isinstance(fixture, dict) and fixture.get("verified_unchanged") is True,
        "Fixtures were not verified unchanged",
    )
    require(fixture.get("sha256") == expected["sha256"], "Receipt belongs to other source fixtures")


def _installed_receipt(
    directory: Path, artifact: dict[str, Any], python: str, root: Path
) -> dict[str, Any]:
    acceptance = read_object(directory / "acceptance-metadata.json")
    conformance = read_object(directory / "conformance.json")
    for value, schema, script in (
        (acceptance, "ipe.installed-acceptance/1", "check_installed_artifact.py"),
        (conformance, "ipe.conformance/1", "check_conformance.py"),
    ):
        _require_receipt_identity(value, schema, python)
        _require_artifact(value, artifact)
        harness = object_value(value.get("harness"), "Missing harness identity")
        require(
            harness.get("sha256") == file_info(root / "scripts" / script)["sha256"],
            "Receipt harness differs from source",
        )
    _require_fixture(acceptance, check_installed_artifact.fixture_fingerprint(root))
    _require_fixture(conformance, check_conformance.fixture_fingerprint(root))
    accepted_ipe = object_value(acceptance.get("ipe"), "Missing installed executable identity")
    checked_ipe = object_value(conformance.get("ipe"), "Missing conformance executable identity")
    accepted_path = text_value(accepted_ipe.get("path"), "Missing installed executable path")
    checked_path = text_value(checked_ipe.get("path"), "Missing conformance executable path")
    require(accepted_path == checked_path, "Conformance used another executable")
    require(
        accepted_ipe.get("version") == f"ipe {project_version(root)}",
        "Wrong installed engine version",
    )
    require(acceptance["python"] == conformance["python"], "Conformance used another environment")
    return {
        "python": python,
        "artifact": artifact["name"],
        "acceptance": file_info(directory / "acceptance-metadata.json"),
        "conformance": file_info(directory / "conformance.json"),
    }


def _quality_receipts(directory: Path, python: str, root: Path) -> dict[str, Any]:
    tests = ET.parse(directory / "tests.xml").getroot()
    suites = list(tests.iter("testsuite"))
    counts = {
        field: sum(int(suite.get(field, "0")) for suite in suites)
        for field in ("tests", "failures", "errors", "skipped")
    }
    require(
        counts["tests"] > 0 and all(counts[key] == 0 for key in ("failures", "errors", "skipped")),
        "Tests failed or were skipped",
    )
    coverage = object_value(
        read_object(directory / "coverage.json").get("totals"), "Missing coverage totals"
    )
    percent = coverage.get("percent_covered")
    if isinstance(percent, bool) or not isinstance(percent, (int, float)):
        raise CandidateError("Invalid coverage percentage")
    require(
        90 <= percent <= 100 and math.isfinite(percent),
        "Coverage is below 90 percent or invalid",
    )
    documentation = read_object(directory / "documented-examples.json")
    require(
        documentation.get("schema") == "ipe.documented-examples/1",
        "Unsupported documentation receipt",
    )
    require(documentation.get("status") == "passed", "Documented examples failed")
    interpreter = text_value(documentation.get("python"), "Missing documentation interpreter")
    require(interpreter.startswith(python + "."), "Wrong documentation interpreter")
    _require_fixture(documentation, check_documented_examples.fingerprint(root))
    require(
        documentation.get("harness_sha256")
        == file_info(root / "scripts/check_documented_examples.py")["sha256"],
        "Documentation harness differs from source",
    )
    blocks = check_documented_examples.registered_blocks(root)
    expected_blocks = {
        block.identifier
        for block in blocks
        if python == "3.12"
        or block.language == "python"
        or check_documented_examples.BASH_POLICIES[block.document][block.ordinal] is None
    }
    completed = documentation.get("completed_blocks")
    require(
        isinstance(completed, list)
        and all(isinstance(identifier, str) for identifier in completed)
        and len(completed) == len(expected_blocks)
        and set(completed) == expected_blocks,
        "Documentation did not complete the expected source blocks",
    )
    executions = documentation.get("executions")
    require(
        isinstance(executions, list)
        and bool(executions)
        and all(
            isinstance(record, dict) and record.get("status") == "passed" for record in executions
        ),
        "Documentation commands failed or are missing",
    )
    if python == "3.12":
        require(
            documentation.get("include_delegated") is True
            and documentation.get("execution_scope") == "all_registered_blocks"
            and documentation.get("all_executable_examples_executed") is True,
            "Full documentation execution is missing",
        )
        require(
            documentation.get("source_verified_unchanged") is True
            and documentation.get("copied_source_verified_unchanged") is True,
            "Documentation execution changed its sources",
        )
        _require_fixture(
            {"fixtures": documentation.get("copied_fixtures")},
            check_documented_examples.fingerprint(root),
        )
        observed = object_value(documentation.get("source_files"), "Missing documentation sources")
        normalized = {
            name: digest for name, digest in observed.items() if name not in DELIVERY_RECORDS
        }
        require(normalized == source_state(root)["files"], "Documentation used other source files")
    return {
        "python": python,
        "tests": counts,
        "coverage_percent": percent,
        "documentation": file_info(directory / "documented-examples.json"),
        "junit": file_info(directory / "tests.xml"),
        "coverage": file_info(directory / "coverage.json"),
        "full_documentation": documentation.get("include_delegated") is True,
    }


def verify(directory: Path, receipts: Path, root: Path) -> dict[str, Any]:
    manifest = read_object(directory / "build-manifest.json")
    require(manifest.get("schema") == "ipe.release-inputs/1", "Unsupported build manifest")
    require(prepare(directory, root) == manifest, "Build inputs or source revision changed")
    quality, installed = [], []
    for python in PYTHONS:
        job = receipts / ("python-" + python)
        quality.append(_quality_receipts(job, python, root))
        for artifact in manifest["artifacts"]:
            installed.append(
                _installed_receipt(job / "installed" / artifact["name"], artifact, python, root)
            )
    return {
        "schema": "ipe.release-candidate/1",
        "status": "automated_checks_passed",
        "version": manifest["version"],
        "source": manifest["source"],
        "build_manifest": file_info(directory / "build-manifest.json"),
        "artifacts": manifest["artifacts"],
        "runtime_requirements": manifest["runtime_requirements"],
        "quality": quality,
        "installed": installed,
        "publication_authorized": False,
        "open_acceptance": [
            "Independent human onboarding and mock walkthrough",
            "Domain-owner acceptance of the scoped process pack",
            "Independent security review and disposition",
            "Disposition of synthetic zero-slowdown benchmark failures",
            "Release owner review and publication decision",
        ],
    }


def write_new(path: Path, value: dict[str, Any]) -> None:
    content = json.dumps(value, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8") as stream:
        stream.write(content)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("prepare", "verify"))
    parser.add_argument("--artifacts", type=Path, required=True)
    parser.add_argument("--receipts", type=Path)
    parser.add_argument("--source", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args(argv)
    if args.operation == "verify" and (args.receipts is None or args.output is None):
        parser.error("verify requires --receipts and --output")
    output = args.output or args.artifacts / "build-manifest.json"
    try:
        require(not output.exists() and not output.is_symlink(), "Output already exists")
        result = (
            prepare(args.artifacts, args.source)
            if args.operation == "prepare"
            else verify(args.artifacts, args.receipts, args.source)
        )
        write_new(output, result)
    except (
        OSError,
        ValueError,
        subprocess.SubprocessError,
        tarfile.TarError,
        zipfile.BadZipFile,
        ET.ParseError,
    ) as error:
        print(f"Candidate rejected: {error}", file=sys.stderr)
        return 1
    print(f"Candidate {args.operation} passed: {output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
