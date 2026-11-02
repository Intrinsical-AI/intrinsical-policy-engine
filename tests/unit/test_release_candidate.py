"""A candidate must bind both artifacts to successful, current matrix receipts."""

import io
import json
import subprocess
import sys
import tarfile
import zipfile

import pytest

from scripts import release_candidate as candidate


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def release_inputs(tmp_path, monkeypatch):
    root = tmp_path / "source"
    package = root / "src/intrinsical_policy_engine"
    package.mkdir(parents=True)
    (package / "__init__.py").write_text('"""Example engine."""\n')
    (package / "py.typed").write_bytes(b"")
    (root / "pyproject.toml").write_text(
        '[project]\nname="intrinsical-policy-engine"\nversion="3.0.0"\n'
    )
    (root / "README.md").write_text("Example documentation\n")
    scripts = root / "scripts"
    scripts.mkdir()
    for name in (
        "check_installed_artifact.py",
        "check_conformance.py",
        "check_documented_examples.py",
    ):
        (scripts / name).write_text('"""Acceptance harness."""\n')
    artifacts = tmp_path / "dist"
    artifacts.mkdir()
    (artifacts / "runtime-requirements.txt").write_text("example==1\n")
    wheel = artifacts / "intrinsical_policy_engine-3.0.0-py3-none-any.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(
            "intrinsical_policy_engine/__init__.py", (package / "__init__.py").read_bytes()
        )
        archive.writestr("intrinsical_policy_engine/py.typed", b"")
    sdist = artifacts / "intrinsical_policy_engine-3.0.0.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        for name in (
            "src/intrinsical_policy_engine/__init__.py",
            "src/intrinsical_policy_engine/py.typed",
            "pyproject.toml",
            "README.md",
        ):
            data = (root / name).read_bytes()
            member = tarfile.TarInfo("intrinsical_policy_engine-3.0.0/" + name)
            member.size = len(data)
            archive.addfile(member, io.BytesIO(data))

    def source_state(directory):
        return {
            "revision": "a" * 40,
            "files": {
                p.relative_to(directory).as_posix(): candidate.sha256(p.read_bytes())
                for p in sorted(directory.rglob("*"))
                if p.is_file()
            },
        }

    monkeypatch.setattr(candidate, "source_state", source_state)
    for module in (candidate.check_conformance, candidate.check_installed_artifact):
        monkeypatch.setattr(module, "fixture_fingerprint", lambda _root: {"sha256": "fixture-sha"})
    monkeypatch.setattr(
        candidate.check_documented_examples, "fingerprint", lambda _root: {"sha256": "fixture-sha"}
    )
    monkeypatch.setattr(
        candidate.check_documented_examples,
        "registered_blocks",
        lambda _root: [
            candidate.check_documented_examples.Block("README.md", "bash", 0, 1, "true\n")
        ],
    )
    manifest = candidate.prepare(artifacts, root)
    write_json(artifacts / "build-manifest.json", manifest)
    receipts = tmp_path / "receipts"
    for python in candidate.PYTHONS:
        job = receipts / ("python-" + python)
        job.mkdir(parents=True)
        (job / "tests.xml").write_text(
            '<testsuites><testsuite tests="4" failures="0" errors="0" skipped="0"/></testsuites>'
        )
        write_json(job / "coverage.json", {"totals": {"percent_covered": 94}})
        write_json(
            job / "documented-examples.json",
            {
                "status": "passed",
                "schema": "ipe.documented-examples/1",
                "python": python + ".15",
                "source_files": source_state(root)["files"],
                "completed_blocks": ["README.md#bash-1"],
                "executions": [{"status": "passed"}],
                "fixtures": {"sha256": "fixture-sha", "verified_unchanged": True},
                "copied_fixtures": {"sha256": "fixture-sha", "verified_unchanged": True},
                "harness_sha256": candidate.file_info(scripts / "check_documented_examples.py")[
                    "sha256"
                ],
                "include_delegated": python == "3.12",
                "execution_scope": "all_registered_blocks",
                "all_executable_examples_executed": True,
                "source_verified_unchanged": True,
                "copied_source_verified_unchanged": True,
            },
        )
        for artifact in manifest["artifacts"]:
            destination = job / "installed" / artifact["name"]
            for filename, schema, script in (
                (
                    "acceptance-metadata.json",
                    "ipe.installed-acceptance/1",
                    "check_installed_artifact.py",
                ),
                ("conformance.json", "ipe.conformance/1", "check_conformance.py"),
            ):
                write_json(
                    destination / filename,
                    {
                        "schema": schema,
                        "schema_version": 1,
                        "status": "passed",
                        "python": {
                            "version": python + ".15",
                            "executable": "/isolated/venv/bin/python",
                        },
                        "ipe": (
                            {"path": "/isolated/venv/bin/ipe", "version": "ipe 3.0.0"}
                            if filename == "acceptance-metadata.json"
                            else {"path": "/isolated/venv/bin/ipe", "sha256": "a" * 64, "size": 123}
                        ),
                        "artifact": {
                            "path": "/builder/" + artifact["name"],
                            "sha256": artifact["sha256"],
                            "size": artifact["size"],
                        },
                        "artifact_verified_unchanged": True,
                        "harness": {"sha256": candidate.file_info(scripts / script)["sha256"]},
                        "fixtures": {"sha256": "fixture-sha", "verified_unchanged": True},
                    },
                )
    return root, artifacts, receipts


def test_one_pair_is_accepted_on_both_interpreters_without_authorizing_publication(release_inputs):
    root, artifacts, receipts = release_inputs
    report = candidate.verify(artifacts, receipts, root)
    assert report["status"] == "automated_checks_passed"
    assert len(report["artifacts"]) == 2
    assert len(report["installed"]) == 4
    assert report["publication_authorized"] is False
    assert report["open_acceptance"]


def test_typed_distribution_requires_the_source_marker(release_inputs):
    root, artifacts, _ = release_inputs
    (root / "src/intrinsical_policy_engine/py.typed").unlink()
    with pytest.raises(candidate.CandidateError, match=r"py\.typed"):
        candidate.distribution_inventory(artifacts, root)


@pytest.mark.parametrize("kind", ["wheel", "sdist"])
@pytest.mark.parametrize("change", ["missing", "tampered"])
def test_distribution_marker_must_match_source(release_inputs, kind, change):
    root, artifacts, _ = release_inputs
    if kind == "wheel":
        path = next(artifacts.glob("*.whl"))
        with zipfile.ZipFile(path) as archive:
            members = {name: archive.read(name) for name in archive.namelist()}
        name = "intrinsical_policy_engine/py.typed"
        if change == "missing":
            del members[name]
        else:
            members[name] = b"partial\n"
        with zipfile.ZipFile(path, "w") as archive:
            for name, data in members.items():
                archive.writestr(name, data)
    else:
        path = next(artifacts.glob("*.tar.gz"))
        with tarfile.open(path) as archive:
            members = {
                member.name: archive.extractfile(member).read() for member in archive.getmembers()
            }
        name = "intrinsical_policy_engine-3.0.0/src/intrinsical_policy_engine/py.typed"
        if change == "missing":
            del members[name]
        else:
            members[name] = b"partial\n"
        with tarfile.open(path, "w:gz") as archive:
            for name, data in members.items():
                member = tarfile.TarInfo(name)
                member.size = len(data)
                archive.addfile(member, io.BytesIO(data))
    with pytest.raises(candidate.CandidateError, match="Distribution sources differ"):
        candidate.distribution_inventory(artifacts, root)


@pytest.mark.parametrize(
    "filename,key,value",
    [
        ("acceptance-metadata.json", "status", "failed"),
        ("conformance.json", "schema_version", True),
        ("conformance.json", "artifact_verified_unchanged", False),
        ("conformance.json", "ipe", "/other/ipe"),
        ("conformance.json", "artifact", {"path": "other.whl", "sha256": "0" * 64, "size": 1}),
        ("conformance.json", "artifact", None),
        ("conformance.json", "harness", {"sha256": "stale"}),
        ("conformance.json", "fixtures", {"sha256": "wrong", "verified_unchanged": True}),
        ("conformance.json", "fixtures", {"sha256": "fixture-sha", "verified_unchanged": False}),
        (
            "acceptance-metadata.json",
            "python",
            {"version": "3.12.1", "executable": "/isolated/venv/bin/python"},
        ),
    ],
)
def test_claimed_green_cannot_override_failed_or_unbound_acceptance(
    release_inputs, filename, key, value
):
    root, artifacts, receipts = release_inputs
    path = next((receipts / "python-3.11/installed").glob("*/" + filename))
    report = json.loads(path.read_text())
    report[key] = value
    write_json(path, report)
    with pytest.raises(candidate.CandidateError):
        candidate.verify(artifacts, receipts, root)


def test_missing_matrix_cell_is_rejected(release_inputs):
    root, artifacts, receipts = release_inputs
    next((receipts / "python-3.12/installed").glob("*/conformance.json")).unlink()
    with pytest.raises(candidate.CandidateError):
        candidate.verify(artifacts, receipts, root)


@pytest.mark.parametrize("change", ["artifact", "source", "requirements"])
def test_changed_build_input_is_rejected(release_inputs, change):
    root, artifacts, receipts = release_inputs
    path = {
        "artifact": next(artifacts.glob("*.whl")),
        "source": root / "README.md",
        "requirements": artifacts / "runtime-requirements.txt",
    }[change]
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(candidate.CandidateError):
        candidate.verify(artifacts, receipts, root)


@pytest.mark.parametrize("kind", ["coverage", "skipped", "docs-syntax-only", "docs-changed-source"])
def test_quality_receipts_must_prove_the_required_scope(release_inputs, kind):
    root, artifacts, receipts = release_inputs
    job = receipts / "python-3.12"
    if kind == "coverage":
        write_json(job / "coverage.json", {"totals": {"percent_covered": 89.9}})
    elif kind == "skipped":
        (job / "tests.xml").write_text(
            '<testsuites><testsuite tests="4" skipped="1"/></testsuites>'
        )
    else:
        path = job / "documented-examples.json"
        data = json.loads(path.read_text())
        data["include_delegated" if kind == "docs-syntax-only" else "source_verified_unchanged"] = (
            False
        )
        write_json(path, data)
    with pytest.raises(candidate.CandidateError):
        candidate.verify(artifacts, receipts, root)


def test_cli_preserves_existing_candidate_file(release_inputs, tmp_path):
    root, artifacts, receipts = release_inputs
    output = tmp_path / "candidate.json"
    output.write_bytes(b"owned")
    assert (
        candidate.main(
            [
                "verify",
                "--source",
                str(root),
                "--artifacts",
                str(artifacts),
                "--receipts",
                str(receipts),
                "--output",
                str(output),
            ]
        )
        == 1
    )
    assert output.read_bytes() == b"owned"


def test_source_inventory_includes_untracked_and_removed_paths_without_changing_index(tmp_path):
    def git(*arguments):
        return subprocess.check_output(["git", "-C", str(tmp_path), *arguments])

    git("init", "-q")
    (tmp_path / "tracked.txt").write_text("baseline")
    git("add", "tracked.txt")
    git(
        "-c",
        "user.name=Test",
        "-c",
        "user.email=test@example.test",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "test baseline",
    )
    (tmp_path / "tracked.txt").unlink()
    (tmp_path / "new.txt").write_text("current")
    index = (tmp_path / ".git/index").read_bytes()
    state = candidate.source_state(tmp_path)
    assert state["dirty"] is True
    assert state["files"] == {"new.txt": candidate.sha256(b"current")}
    assert len(state["revision"]) == 40
    assert (tmp_path / ".git/index").read_bytes() == index


@pytest.mark.parametrize("safe_path", [False, True])
@pytest.mark.parametrize("script", ["release_candidate.py", "check_conformance.py"])
def test_documented_direct_script_entrypoint_works_outside_checkout(
    tmp_path, monkeypatch, safe_path, script
):
    if safe_path:
        monkeypatch.setenv("PYTHONSAFEPATH", "1")
    else:
        monkeypatch.delenv("PYTHONSAFEPATH", raising=False)
    result = subprocess.run(
        [sys.executable, str(candidate.ROOT / "scripts" / script), "--help"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    if script == "release_candidate.py":
        assert "prepare" in result.stdout and "verify" in result.stdout
    else:
        assert "--fixtures" in result.stdout and "--artifact" in result.stdout


@pytest.mark.parametrize(
    "key,value",
    [
        ("python", "2.7.18"),
        ("source_files", {"src/intrinsical_policy_engine/__init__.py": "0" * 64}),
        ("completed_blocks", []),
        ("completed_blocks", ["README.md#bash-1", "README.md#bash-1"]),
        ("executions", [{"status": "failed"}]),
        ("executions", []),
        ("harness_sha256", "0" * 64),
    ],
)
def test_documentation_claims_are_bound_to_actual_scope_and_sources(release_inputs, key, value):
    root, artifacts, receipts = release_inputs
    path = receipts / "python-3.12/documented-examples.json"
    data = json.loads(path.read_text())
    data[key] = value
    write_json(path, data)
    with pytest.raises(candidate.CandidateError):
        candidate.verify(artifacts, receipts, root)
