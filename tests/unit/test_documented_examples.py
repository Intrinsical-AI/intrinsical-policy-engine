# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Exact documentation workflows run without inventing human acceptance."""

from __future__ import annotations

import json
import os
import shlex
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from scripts import check_documented_examples as examples


@pytest.fixture
def copied_checkout(tmp_path: Path) -> Path:
    root = tmp_path / "fixtures"
    root.mkdir()
    names = set(examples.BASH_POLICIES) | {"pyproject.toml", "uv.lock"}
    for name in names:
        destination = root / name
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(examples.ROOT / name, destination)
    for name in examples.FIXTURE_DIRECTORIES:
        shutil.copytree(examples.ROOT / name, root / name, dirs_exist_ok=True)
    return root


def test_exact_registered_workflows_and_api_example_execute_without_fixture_changes(tmp_path):
    before = examples.fingerprint(examples.ROOT)
    destination = tmp_path / "exact-docs"
    receipt = examples.run_examples(destination)
    assert receipt["status"] == "passed", receipt.get("error")
    assert receipt["fixtures"]["verified_unchanged"] is True
    assert examples.fingerprint(examples.ROOT) == before
    assert json.loads((destination / "documented-examples.json").read_text()) == receipt
    assert len(receipt["syntax_checks"]) == 18
    assert len(receipt["executions"]) == 4
    executed = {block for result in receipt["executions"] for block in result["blocks"]}
    assert executed == {
        "README.md#bash-1",
        *(f"docs/WRITE_YOUR_FIRST_PACK.md#bash-{index}" for index in range(1, 7)),
        "docs/CLI_REFERENCE.md#bash-1",
        "docs/CLI_REFERENCE.md#bash-2",
        "docs/POLICY_SCHEMA.md#python-1",
    }
    assert all(result["status"] == "passed" for result in receipt["executions"])
    delegated = {item["block"]: item["gate"] for item in receipt["delegated_blocks"]}
    assert delegated["README.md#bash-3"] == "development-validation"
    assert delegated["docs/CLI_REFERENCE.md#bash-3"] == "signed-acceptance"
    assert not executed.intersection(delegated)
    assert all(item["status"] == "not_executed_here" for item in receipt["delegated_blocks"])
    assert receipt["include_delegated"] is False
    assert receipt["all_executable_examples_executed"] is False
    assert set(receipt["completed_blocks"]) == executed
    assert any((destination / "tmp").iterdir())


def test_invalid_bash_fails_before_execution_with_a_failure_receipt(copied_checkout, tmp_path):
    readme = copied_checkout / "README.md"
    readme.write_text(readme.read_text().replace("uv sync --locked", "if then"))
    destination = tmp_path / "syntax-failure"
    receipt = examples.run_examples(destination, root=copied_checkout)
    assert receipt["status"] == "failed"
    assert receipt["executions"] == []
    assert receipt["syntax_checks"][0]["status"] == "failed"
    assert receipt["fixtures"]["verified_unchanged"] is True
    assert (destination / "documented-examples.json").is_file()


def test_new_documented_fence_requires_an_execution_policy(copied_checkout):
    readme = copied_checkout / "README.md"
    readme.write_text(readme.read_text() + "\n```bash\nprintf unregistered\n```\n")
    with pytest.raises(examples.ExampleError, match="execution policies"):
        examples.registered_blocks(copied_checkout)


def test_new_document_cannot_silently_escape_the_registry(copied_checkout):
    (copied_checkout / "docs/new-example.md").write_text("```bash\nexit 9\n```\n")
    with pytest.raises(examples.ExampleError, match="registry"):
        examples.registered_blocks(copied_checkout)


def test_unclosed_executable_fence_is_rejected(copied_checkout):
    readme = copied_checkout / "BOUNDARIES.md"
    readme.write_text(readme.read_text() + "\n```bash\nprintf incomplete\n")
    with pytest.raises(examples.ExampleError, match="Unclosed code fence"):
        examples.registered_blocks(copied_checkout)


def test_command_timeout_produces_a_failed_check(tmp_path, monkeypatch):
    monkeypatch.setattr(examples, "COMMAND_TIMEOUT", 0.05)
    result = examples.run_command(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        cwd=tmp_path,
        environment=dict(os.environ),
    )
    assert result["status"] == "timed_out"
    assert result["returncode"] != 0
    assert result["duration_seconds"] < 5


def test_fixture_change_never_produces_a_passing_receipt(copied_checkout, tmp_path, monkeypatch):
    def mutate_fixture(command, *, cwd, environment, source=None, timeout=None):
        (cwd / "demos/starter/basic/answers.json").write_text("changed during execution")
        return {"status": "passed", "returncode": 0, "command": command}

    monkeypatch.setattr(examples, "run_command", mutate_fixture)
    receipt = examples.run_examples(tmp_path / "fixture-change", root=copied_checkout)
    assert receipt["status"] == "failed"
    assert receipt["fixtures"]["verified_unchanged"] is False
    assert "changed during execution" in receipt["error"]


def test_failing_exact_example_remains_failed_when_fixtures_are_unchanged(
    copied_checkout, tmp_path, monkeypatch
):
    def fail_execution(command, *, cwd, environment, source=None, timeout=None):
        if command == ["bash", "-n"]:
            return {"status": "passed", "returncode": 0, "command": command}
        return {"status": "failed", "returncode": 7, "command": command}

    monkeypatch.setattr(examples, "run_command", fail_execution)
    receipt = examples.run_examples(tmp_path / "failed-command", root=copied_checkout)
    assert receipt["status"] == "failed"
    assert receipt["executions"][0]["returncode"] == 7
    assert receipt["fixtures"]["verified_unchanged"] is True


def test_existing_workdir_is_preserved(tmp_path):
    destination = tmp_path / "existing"
    destination.mkdir()
    owned = destination / "owned.txt"
    owned.write_bytes(b"preserve")
    with pytest.raises(examples.ExampleError, match="new directory"):
        examples.run_examples(destination)
    assert list(destination.iterdir()) == [owned]
    assert owned.read_bytes() == b"preserve"


def test_workdir_cannot_be_created_inside_fixture_checkout(copied_checkout):
    with pytest.raises(examples.ExampleError, match="outside the checkout"):
        examples.run_examples(copied_checkout / "output", root=copied_checkout)
    assert not (copied_checkout / "output").exists()


def example_checkout(tmp_path, monkeypatch, bodies, policies):
    """Small independent docs avoid recursively invoking the development recipe."""
    root = tmp_path / "source"
    (root / "docs").mkdir(parents=True)
    (root / "pyproject.toml").write_text("")
    (root / "uv.lock").write_text("")
    (root / "docs/example.md").write_text(
        "\n".join("```bash\n" + body + "\n```\n" for body in bodies)
    )
    monkeypatch.setattr(examples, "BASH_POLICIES", {"docs/example.md": tuple(policies)})
    monkeypatch.setattr(examples, "PYTHON_COUNTS", {"docs/example.md": 0})
    monkeypatch.setattr(examples, "FIXTURE_DIRECTORIES", ())
    return root


def test_full_mode_catches_an_invalid_flag_in_a_previously_delegated_example(tmp_path, monkeypatch):
    source = next(
        block
        for block in examples.registered_blocks(examples.ROOT)
        if block.identifier == "docs/POLICY_SCHEMA.md#bash-1"
    )
    # Use the real schema CLI in a minimal fixture; this test mutates its flag,
    # while the production runner never substitutes any documented command.
    command = shlex.join([sys.executable, str(examples.ROOT / "scripts/export_schemas.py")])
    body = source.body.replace("uv run --frozen python scripts/export_schemas.py", command)
    body = body.replace("--check", "--missing-schema-option")
    root = example_checkout(tmp_path, monkeypatch, [body], ["schema-sync"])
    quick = examples.run_examples(tmp_path / "quick", root=root)
    assert quick["status"] == "passed"
    assert quick["completed_blocks"] == []
    full = examples.run_examples(tmp_path / "full", root=root, include_delegated=True)
    assert full["status"] == "failed"
    assert full["syntax_checks"][0]["status"] == "passed"
    assert full["executions"][0]["returncode"] == 2
    assert "unrecognized arguments" in full["executions"][0]["stderr"]
    assert not full["all_executable_examples_executed"]


def test_full_mode_preserves_shell_variables_and_completes_every_block(tmp_path, monkeypatch):
    root = example_checkout(
        tmp_path,
        monkeypatch,
        ["answer=42", 'test "$answer" = 42'],
        [None, "schema-sync"],
    )
    receipt = examples.run_examples(tmp_path / "full", root=root, include_delegated=True)
    assert receipt["status"] == "passed", receipt.get("error")
    assert receipt["completed_blocks"] == ["docs/example.md#bash-1", "docs/example.md#bash-2"]
    assert receipt["all_executable_examples_executed"]
    assert receipt["delegated_blocks"][0]["status"] == "executed_here"


def test_early_successful_shell_exit_cannot_claim_later_blocks_executed(tmp_path, monkeypatch):
    root = example_checkout(tmp_path, monkeypatch, ["exit 0", "printf skipped"], [None, None])
    receipt = examples.run_examples(tmp_path / "early-exit", root=root, include_delegated=True)
    assert receipt["status"] == "failed"
    assert receipt["executions"][0]["returncode"] == 0
    assert receipt["completed_blocks"] == []
    assert not receipt["all_executable_examples_executed"]


def test_development_recipe_writes_only_a_disposable_copy(tmp_path, monkeypatch):
    root = example_checkout(
        tmp_path,
        monkeypatch,
        ["mkdir -p dist build\nprintf generated > dist/new\nprintf generated > build/new"],
        ["development-validation"],
    )
    (root / ".gitignore").write_text("dist/\nbuild/\n.venv/\n")
    for directory in ("dist", "build"):
        (root / directory).mkdir()
        (root / directory / "owned").write_bytes(b"preserve")
    subprocess.run(["git", "-c", "init.templateDir=", "init", "--quiet"], cwd=root, check=True)
    git_config = (root / ".git/config").read_bytes()
    # Inherited repository selectors must not redirect disposable Git commands
    # into the caller's metadata or index.
    monkeypatch.setenv("GIT_DIR", str(root / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(root))
    monkeypatch.setenv("GIT_INDEX_FILE", str(root / ".git/owned-index"))
    receipt = examples.run_examples(tmp_path / "full", root=root, include_delegated=True)
    assert receipt["status"] == "passed", receipt.get("error")
    assert receipt["source_verified_unchanged"]
    assert receipt["copied_source_verified_unchanged"]
    assert receipt["fixtures"]["verified_unchanged"]
    assert receipt["copied_fixtures"]["verified_unchanged"]
    assert (root / ".git/config").read_bytes() == git_config
    assert not (root / ".git/owned-index").exists()
    for directory in ("dist", "build"):
        assert list((root / directory).iterdir()) == [root / directory / "owned"]
        assert (root / directory / "owned").read_bytes() == b"preserve"
        assert (
            Path(receipt["execution_checkout"]) / directory / "new"
        ).read_bytes() == b"generated"


def test_same_checkout_full_mode_cannot_recurse(tmp_path, monkeypatch):
    monkeypatch.setenv(examples.FULL_ROOT_ENVIRONMENT, str(examples.ROOT))
    with pytest.raises(examples.ExampleError, match="already active"):
        examples.run_examples(tmp_path / "recursive", include_delegated=True)
    assert not (tmp_path / "recursive").exists()


def test_failed_copy_preparation_retains_the_command_and_planned_scope(tmp_path, monkeypatch):
    root = example_checkout(tmp_path, monkeypatch, [":"], ["development-validation"])
    subprocess.run(["git", "-c", "init.templateDir=", "init", "--quiet"], cwd=root, check=True)

    def fail_git(command, *, cwd, environment, source=None, timeout=None):
        return {
            "command": command,
            "status": "failed",
            "returncode": 128,
            "stdout": "",
            "stderr": "injected Git preparation failure",
        }

    monkeypatch.setattr(examples, "run_command", fail_git)
    receipt = examples.run_examples(tmp_path / "failed-copy", root=root, include_delegated=True)
    assert receipt["status"] == "failed"
    assert receipt["blocks"][0]["id"] == "docs/example.md#bash-1"
    assert receipt["delegated_blocks"][0]["status"] == "not_executed_here"
    assert receipt["working_copy_preparation"][0]["returncode"] == 128
    assert "injected Git" in receipt["working_copy_preparation"][0]["stderr"]
    assert not receipt["all_executable_examples_executed"]


def test_gpg_cleanup_targets_only_the_generated_private_home(tmp_path, monkeypatch):
    temporary = tmp_path / "private-tmp"
    home = temporary / "test-signing" / "gnupg"
    (home / "private-keys-v1.d").mkdir(parents=True)
    calls = []

    def record(command, *, cwd, environment, source=None, timeout=None):
        calls.append(command)
        return {"command": command, "status": "passed"}

    monkeypatch.setattr(examples, "run_command", record)
    result = examples.cleanup_gpg(temporary, {"GNUPGHOME": "/unrelated/user/keys"})
    assert calls == [["gpgconf", "--homedir", str(home), "--kill", "gpg-agent"]]
    assert result[0]["home"] == str(home)
