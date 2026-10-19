"""Acceptance probes use an external CLI and receipts detect changing inputs."""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path

import pytest

from scripts import check_installed_artifact as acceptance


@pytest.fixture
def executable() -> Path:
    return Path(sys.executable).parent / "ipe"


def test_regression_probes_run_outside_checkout_without_changing_source_fixtures(
    executable: Path, tmp_path: Path
) -> None:
    fixtures = Path(__file__).resolve().parents[2]
    before = acceptance.fixture_fingerprint(fixtures)
    workdir = tmp_path / "external"
    workdir.mkdir()
    runner = acceptance.Runner(executable, workdir)
    acceptance.run_regression_acceptance(runner, fixtures)
    assert acceptance.fixture_fingerprint(fixtures) == before
    checks = {record["check"] for record in runner.records}
    assert {
        "arbitrary offline headers withheld",
        "relative calendar overflow rejected",
        "maximum calendar date exported",
        "malformed CSV evidence rejected",
        "empty CSV header rejected",
        "empty CSV rows rejected",
        "front matter ending at EOF rejected",
        "unterminated log line separated",
    } <= checks
    assert any(check.startswith("invalid nested exporter ID rejected") for check in checks)
    assert any(check.startswith("HTTP control characters rejected") for check in checks)
    assert all(Path(record["command"][0]).is_absolute() for record in runner.records)


@pytest.fixture
def minimal_fixtures(tmp_path: Path) -> Path:
    fixtures = tmp_path / "source"
    for directory in ("frameworks/starter", "demos/starter/basic", "demos/starter/configs"):
        (fixtures / directory).mkdir(parents=True)
    (fixtures / "pyproject.toml").write_text('[project]\nversion = "3.0.0"\n')
    (fixtures / "frameworks/starter/manifest.yml").write_text("framework_id: receipt-fixture\n")
    (fixtures / "demos/starter/basic/answers.json").write_text('{"flags": []}')
    return fixtures


def invoke_receipt(
    monkeypatch: pytest.MonkeyPatch,
    executable: Path,
    fixtures: Path,
    workdir: Path,
    artifact: Path,
) -> int:
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "check_installed_artifact.py",
            "--ipe",
            str(executable),
            "--fixtures",
            str(fixtures),
            "--workdir",
            str(workdir),
            "--artifact",
            str(artifact),
        ],
    )
    return acceptance.main()


def test_receipt_records_checked_inputs_and_preserves_the_command_list_schema(
    monkeypatch: pytest.MonkeyPatch, executable: Path, minimal_fixtures: Path, tmp_path: Path
) -> None:
    artifact = tmp_path / "release.whl"
    payload = b"acceptance artifact fixture"
    artifact.write_bytes(payload)

    def one_external_check(runner: acceptance.Runner, _fixtures: Path) -> None:
        runner.run("version", "--version")

    monkeypatch.setattr(acceptance, "run_acceptance", one_external_check)
    workdir = tmp_path / "receipt"
    assert invoke_receipt(monkeypatch, executable, minimal_fixtures, workdir, artifact) == 0
    metadata = json.loads((workdir / "acceptance-metadata.json").read_text())
    assert metadata["schema"] == "ipe.installed-acceptance/1"
    assert metadata["status"] == "passed"
    assert metadata["artifact"] == {
        "path": str(artifact),
        "sha256": hashlib.sha256(payload).hexdigest(),
        "size": len(payload),
    }
    assert metadata["fixtures"]["verified_unchanged"] is True
    assert metadata["artifact_verified_unchanged"] is True
    assert metadata["python"]["version"]
    assert metadata["ipe"]["path"] == str(executable)
    assert metadata["ipe"]["version"].startswith("ipe ")
    records = json.loads((workdir / "acceptance.json").read_text())
    assert isinstance(records, list) and len(records) == 1
    assert set(records[0]) == {"check", "command", "returncode", "stdout", "stderr"}


@pytest.mark.parametrize("changed_input", ["artifact", "fixtures"])
def test_acceptance_fails_if_an_input_changes_while_the_cli_is_checked(
    monkeypatch: pytest.MonkeyPatch,
    executable: Path,
    minimal_fixtures: Path,
    tmp_path: Path,
    changed_input: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    artifact = tmp_path / "release.whl"
    artifact.write_bytes(b"original artifact")

    def change_after_check(runner: acceptance.Runner, fixtures: Path) -> None:
        runner.run("version", "--version")
        target = artifact if changed_input == "artifact" else fixtures / "pyproject.toml"
        target.write_bytes(b"changed during acceptance")

    monkeypatch.setattr(acceptance, "run_acceptance", change_after_check)
    workdir = tmp_path / "changed-receipt"
    assert invoke_receipt(monkeypatch, executable, minimal_fixtures, workdir, artifact) == 1
    assert "changed during acceptance" in capsys.readouterr().err
    metadata = json.loads((workdir / "acceptance-metadata.json").read_text())
    assert metadata["status"] == "failed"


def runtime_metadata() -> dict:
    return {
        "prefix": "/env",
        "modules": {"intrinsical_policy_engine": "/env/lib/intrinsical_policy_engine/__init__.py"},
        "retained_modules": [],
        "entrypoints": ["intrinsical_policy_engine.app.cli.main:main"],
        "typing_marker": {
            "path": "/env/lib/intrinsical_policy_engine/py.typed",
            "regular_file": True,
        },
    }


def test_import_guard_accepts_only_installed_namespace() -> None:
    acceptance.check_runtime_origins(runtime_metadata())


@pytest.mark.parametrize(
    "marker",
    [
        None,
        {"path": "/env/lib/intrinsical_policy_engine/py.typed", "regular_file": False},
        {"path": "/checkout/src/intrinsical_policy_engine/py.typed", "regular_file": True},
    ],
)
def test_import_guard_requires_a_regular_marker_alongside_installed_package(marker) -> None:
    metadata = runtime_metadata()
    metadata["typing_marker"] = marker
    with pytest.raises(acceptance.AcceptanceError, match=r"py\.typed"):
        acceptance.check_runtime_origins(metadata)


@pytest.mark.parametrize("legacy_package", ["src", "scripts"])
def test_import_guard_rejects_legacy_top_level_packages(legacy_package: str) -> None:
    metadata = runtime_metadata()
    metadata["retained_modules"] = [legacy_package]
    with pytest.raises(acceptance.AcceptanceError, match="retained deleted modules"):
        acceptance.check_runtime_origins(metadata)


def test_import_guard_rejects_source_import_even_when_root_package_is_installed() -> None:
    metadata = runtime_metadata()
    metadata["modules"]["intrinsical_policy_engine.app.cli.main"] = "/checkout/src/main.py"
    with pytest.raises(acceptance.AcceptanceError, match="editable/source install"):
        acceptance.check_runtime_origins(metadata)


def test_import_guard_rejects_stale_console_entrypoint() -> None:
    metadata = runtime_metadata()
    metadata["entrypoints"] = ["src.app.cli.main:main"]
    with pytest.raises(acceptance.AcceptanceError, match="public package"):
        acceptance.check_runtime_origins(metadata)


def test_runner_isolates_python_environment(
    executable: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PYTHONPATH", "/unrelated/source")
    monkeypatch.setenv("PYTHONHOME", "/unrelated/python")
    monkeypatch.setenv("PYTHONINSPECT", "1")
    runner = acceptance.Runner(executable, tmp_path)
    assert "PYTHONPATH" not in runner.env
    assert "PYTHONHOME" not in runner.env
    assert "PYTHONINSPECT" not in runner.env
    assert runner.env["PYTHONNOUSERSITE"] == "1"
    assert runner.env["PYTHONSAFEPATH"] == "1"


def test_installed_acceptance_rejects_this_editable_environment(
    executable: Path, tmp_path: Path
) -> None:
    # This test runs in the checkout's editable development environment. Installed
    # release acceptance must reject it even when all functional CLI probes pass.
    with pytest.raises(acceptance.AcceptanceError, match="editable/source install"):
        acceptance.check_installed_imports(acceptance.Runner(executable, tmp_path))
