# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Check registered documentation syntax and execute exact examples.

The default scope excludes expensive release/signing/development recipes.
--include-delegated executes every registered Bash and Python fence. Run that
mode outside pytest; its development recipe runs pytest with the default scope.
Neither scope establishes human onboarding, policy correctness or pilot acceptance.
"""

from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
COMMAND_TIMEOUT = 180
FULL_COMMAND_TIMEOUT = 500
FULL_ROOT_ENVIRONMENT = "IPE_DOCUMENTED_EXAMPLES_FULL_ROOT"
FENCE = re.compile(r"^```(?P<language>bash|python)[ \t]*\n(?P<body>.*?)^```[ \t]*$", re.M | re.S)
OPENING_FENCE = re.compile(r"^```(?:bash|python)[ \t]*$", re.M)

# Each entry corresponds to a Bash fence in document order. None means execute
# its original bytes; a string identifies the separate gate that owns execution.
BASH_POLICIES: dict[str, tuple[str | None, ...]] = {
    "README.md": (None, "installed-release", "development-validation", "benchmark"),
    "BOUNDARIES.md": (),
    "CHANGELOG.md": (),
    "SECURITY.md": (),
    "docs/AUTHORING_ERRORS.md": (),
    "docs/CLI_REFERENCE.md": (None, None, "signed-acceptance", "installed-release"),
    "docs/MINIMUM_AUTHORING_PATH.md": (),
    "docs/POLICY_SCHEMA.md": ("schema-sync",),
    "docs/SECURITY_AND_LIMITATIONS.md": (),
    "docs/WRITE_YOUR_FIRST_PACK.md": (None, None, None, None, None, None),
    "demos/process-review/README.md": ("process-conformance", "test-suite"),
}
PYTHON_COUNTS = {name: int(name == "docs/POLICY_SCHEMA.md") for name in BASH_POLICIES}
FIXTURE_DIRECTORIES = ("frameworks/starter", "demos/starter", "demos/process-review")


class ExampleError(ValueError):
    """Documentation or an exact example failed its declared check."""


@dataclass(frozen=True)
class Block:
    document: str
    language: str
    ordinal: int
    line: int
    body: str

    @property
    def identifier(self) -> str:
        return f"{self.document}#{self.language}-{self.ordinal + 1}"


def digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def fingerprint(root: Path) -> dict[str, Any]:
    """Hash source documents and every external pack/input/evidence fixture."""
    paths = {root / name for name in BASH_POLICIES}
    paths.update(root / name for name in ("pyproject.toml", "uv.lock"))
    for name in FIXTURE_DIRECTORIES:
        directory = root / name
        if not directory.is_dir() or directory.is_symlink():
            raise ExampleError(f"Missing or unsafe fixture directory: {name}")
        for path in directory.rglob("*"):
            if path.is_symlink():
                raise ExampleError(f"Symbolic link in fixtures: {path.relative_to(root)}")
            if path.is_file():
                paths.add(path)
    files: dict[str, str] = {}
    for path in sorted(paths):
        if not path.is_file() or path.is_symlink():
            raise ExampleError(f"Missing or unsafe fixture: {path.relative_to(root)}")
        files[path.relative_to(root).as_posix()] = digest(path.read_bytes())
    content = json.dumps(files, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return {"sha256": digest(content), "files": files}


def registered_blocks(root: Path) -> list[Block]:
    """Require an explicit disposition for every Bash/Python fence in scope."""
    current = {path.relative_to(root).as_posix() for path in (root / "docs").glob("*.md")}
    expected = {name for name in BASH_POLICIES if name.startswith("docs/")}
    if current != expected:
        raise ExampleError("docs/*.md changed; update the documented-example registry")
    blocks: list[Block] = []
    for name, policies in BASH_POLICIES.items():
        text = (root / name).read_text(encoding="utf-8")
        matches = list(FENCE.finditer(text))
        if len(matches) != len(OPENING_FENCE.findall(text)):
            raise ExampleError(f"Unclosed code fence in {name}")
        counts = {"bash": 0, "python": 0}
        for match in matches:
            language = match["language"]
            blocks.append(
                Block(
                    name,
                    language,
                    counts[language],
                    text.count("\n", 0, match.start()) + 1,
                    match["body"],
                )
            )
            counts[language] += 1
        if counts != {"bash": len(policies), "python": PYTHON_COUNTS[name]}:
            raise ExampleError(f"Code fences changed in {name}; update their execution policies")
    return blocks


def source_files(root: Path) -> dict[str, str]:
    """Describe the current visible working tree without copying Git history or ignored outputs."""
    result = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=root,
        env={key: value for key, value in os.environ.items() if not key.startswith("GIT_")},
        capture_output=True,
        check=False,
        timeout=30,
    )
    if result.returncode:
        raise ExampleError("Full examples require a Git source checkout")
    files = {}
    for name in sorted({os.fsdecode(name) for name in result.stdout.split(b"\0") if name}):
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ExampleError("Unsafe source path in the Git file list")
        path = root / relative
        if not path.exists() and not path.is_symlink():
            continue  # Preserve tracked deletions in the current working tree.
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ExampleError(f"Unsafe source file: {relative}")
        files[relative.as_posix()] = digest(path.read_bytes())
    return files


def prepare_checkout(
    root: Path,
    destination: Path,
    files: dict[str, str],
    environment: dict[str, str],
    results: list[dict[str, Any]],
) -> None:
    """Create a disposable exact working tree and index for literal build/pre-commit recipes."""
    destination.mkdir(mode=0o700)
    for name, expected in files.items():
        target = destination / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target)
        if digest(target.read_bytes()) != expected:
            raise ExampleError("Source changed while preparing the example checkout")
    for command in (
        ["git", "-c", "init.templateDir=", "init", "--quiet"],
        ["git", "-c", "core.hooksPath=/dev/null", "add", "--all"],
    ):
        result = run_command(command, cwd=destination, environment=environment)
        results.append(result)
        require_pass(result, "Disposable Git checkout preparation")
    if source_files(destination) != files:
        raise ExampleError("Disposable checkout differs from the source working tree")


def run_command(
    command: list[str],
    *,
    cwd: Path,
    environment: dict[str, str],
    source: str | None = None,
    timeout: float | None = None,
) -> dict[str, Any]:
    started = time.monotonic()
    timeout = COMMAND_TIMEOUT if timeout is None else timeout
    with subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        text=True,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    ) as process:
        timed_out = False
        try:
            stdout, stderr = process.communicate(source, timeout=timeout)
        except subprocess.TimeoutExpired:
            timed_out = True
            # The process can exit between timeout and group termination.
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        return {
            "command": command,
            "returncode": process.returncode,
            "stdout": stdout,
            "stderr": stderr,
            "duration_seconds": time.monotonic() - started,
            "status": "timed_out"
            if timed_out
            else "passed"
            if process.returncode == 0
            else "failed",
        }


def require_pass(record: dict[str, Any], label: str) -> None:
    if record["status"] != "passed":
        raise ExampleError(f"{label} failed; inspect documented-examples.json")


def check_syntax(
    blocks: list[Block], root: Path, environment: dict[str, str], receipt: dict[str, Any]
) -> None:
    for block in blocks:
        if block.language == "bash":
            result = run_command(
                ["bash", "-n"], cwd=root, environment=environment, source=block.body
            )
        else:
            try:
                ast.parse(block.body, filename=block.identifier)
            except SyntaxError as error:
                raise ExampleError(f"Invalid Python syntax in {block.identifier}") from error
            result = {"status": "passed", "method": "ast.parse"}
        receipt["syntax_checks"].append({"block": block.identifier, **result})
        require_pass(result, block.identifier)


def execute_examples(
    blocks: list[Block],
    root: Path,
    environment: dict[str, str],
    receipt: dict[str, Any],
    progress_dir: Path,
    *,
    include_delegated: bool,
    python_executable: str,
) -> None:
    for document in BASH_POLICIES:
        selected = [
            block
            for block in blocks
            if block.document == document
            and block.language == "bash"
            and (include_delegated or BASH_POLICIES[document][block.ordinal] is None)
        ]
        if selected:
            # Completion markers are outside each unmodified source block. They
            # also detect exit/exec before a later fence in the same shell.
            progress = progress_dir / f"document-{len(receipt['executions'])}.txt"
            progress.touch(mode=0o600, exist_ok=False)
            source = "\n".join(
                block.body
                + "\nprintf '%s\\n' "
                + shlex.quote(block.identifier)
                + " >> "
                + shlex.quote(str(progress))
                + "\n"
                for block in selected
            )
            result = run_command(
                ["bash", "-euo", "pipefail"],
                cwd=root,
                environment=environment,
                source=source,
                timeout=FULL_COMMAND_TIMEOUT if include_delegated else COMMAND_TIMEOUT,
            )
            completed = progress.read_text(encoding="utf-8").splitlines()
            expected = [block.identifier for block in selected]
            result["completed_blocks"] = completed
            if result["status"] == "passed" and completed != expected:
                result["status"] = "failed"
                result["completion_error"] = "Shell exited without completing every source block"
            receipt["executions"].append(
                {"document": document, "blocks": [b.identifier for b in selected], **result}
            )
            receipt["completed_blocks"].extend(completed)
            require_pass(result, document)
        for block in blocks:
            if block.document == document and block.language == "python":
                result = run_command(
                    [python_executable, "-c", block.body], cwd=root, environment=environment
                )
                receipt["executions"].append({"blocks": [block.identifier], **result})
                if result["status"] == "passed":
                    receipt["completed_blocks"].append(block.identifier)
                require_pass(result, block.identifier)


def cleanup_gpg(temporary: Path, environment: dict[str, str]) -> list[dict[str, Any]]:
    """Stop agents only for key homes created beneath this run's private TMPDIR."""
    results = []
    for private_keys in sorted(temporary.rglob("private-keys-v1.d")):
        home = private_keys.parent
        if home.is_symlink() or not home.resolve().is_relative_to(temporary.resolve()):
            raise ExampleError("Unsafe generated GPG home; refusing agent cleanup")
        result = run_command(
            ["gpgconf", "--homedir", str(home), "--kill", "gpg-agent"],
            cwd=temporary,
            environment=environment,
            timeout=10,
        )
        results.append({"home": str(home), **result})
    return results


def configure_execution(
    root: Path,
    workdir: Path,
    environment: dict[str, str],
    receipt: dict[str, Any],
    *,
    include_delegated: bool,
) -> tuple[Path, dict[str, Any] | None, dict[str, str] | None]:
    execution_root = root
    copied_before = None
    visible_source = None
    if include_delegated and any(
        gate == "development-validation" for policies in BASH_POLICIES.values() for gate in policies
    ):
        visible_source = source_files(root)
        receipt["source_files"] = visible_source
        receipt["source_verified_unchanged"] = False
        execution_root = workdir / "checkout"
        receipt["working_copy_preparation"] = []
        prepare_checkout(
            root, execution_root, visible_source, environment, receipt["working_copy_preparation"]
        )
        copied_before = fingerprint(execution_root)
        receipt["copied_fixtures"] = {**copied_before, "verified_unchanged": False}
        environment["UV_PROJECT_ENVIRONMENT"] = str(execution_root / ".venv")
        environment["PRE_COMMIT_HOME"] = str(workdir / "pre-commit-cache")
    receipt["execution_checkout"] = str(execution_root)
    if include_delegated:
        environment[FULL_ROOT_ENVIRONMENT] = str(execution_root)
    if (
        include_delegated
        and any(
            gate == "signed-acceptance" for policies in BASH_POLICIES.values() for gate in policies
        )
        and (shutil.which("gpg") is None or shutil.which("gpgconf") is None)
    ):
        raise ExampleError("Full signing examples require GPG and gpgconf")
    return execution_root, copied_before, visible_source


def finalize_execution(
    root: Path,
    execution_root: Path,
    temporary: Path,
    environment: dict[str, str],
    receipt: dict[str, Any],
    *,
    before: dict[str, Any] | None,
    copied_before: dict[str, Any] | None,
    visible_source: dict[str, str] | None,
) -> None:
    for delegated in receipt["delegated_blocks"]:
        if delegated["block"] in receipt["completed_blocks"]:
            delegated["status"] = "executed_here"
    try:
        receipt["gpg_cleanup"] = cleanup_gpg(temporary, environment)
        if any(result["status"] != "passed" for result in receipt["gpg_cleanup"]):
            receipt["status"] = "failed"
            receipt["cleanup_error"] = "Generated GPG agent cleanup failed"
    except (ExampleError, OSError) as error:
        receipt["status"] = "failed"
        receipt["cleanup_error"] = str(error)
    if before is not None:
        try:
            receipt["fixtures"]["verified_unchanged"] = fingerprint(root) == before
        except (ExampleError, OSError):
            receipt["fixtures"]["verified_unchanged"] = False
        if not receipt["fixtures"]["verified_unchanged"]:
            receipt["status"] = "failed"
            receipt["error"] = "Documentation or source fixtures changed during execution"
    if copied_before is not None:
        try:
            receipt["copied_fixtures"]["verified_unchanged"] = (
                fingerprint(execution_root) == copied_before
            )
        except (ExampleError, OSError):
            receipt["copied_fixtures"]["verified_unchanged"] = False
        if not receipt["copied_fixtures"]["verified_unchanged"]:
            receipt["status"] = "failed"
            receipt["error"] = "Copied documentation or input fixtures changed during execution"
    if visible_source is not None:
        try:
            receipt["source_verified_unchanged"] = source_files(root) == visible_source
            receipt["copied_source_verified_unchanged"] = (
                source_files(execution_root) == visible_source
            )
        except (ExampleError, OSError):
            receipt["source_verified_unchanged"] = False
            receipt["copied_source_verified_unchanged"] = False
        if not (
            receipt["source_verified_unchanged"] and receipt["copied_source_verified_unchanged"]
        ):
            receipt["status"] = "failed"
            receipt["error"] = "Original or copied source working tree changed during execution"


def run_examples(
    workdir: Path, *, root: Path = ROOT, include_delegated: bool = False
) -> dict[str, Any]:
    root = root.resolve(strict=True)
    if include_delegated and os.environ.get(FULL_ROOT_ENVIRONMENT) == str(root):
        raise ExampleError("A full documented-example run is already active for this checkout")
    if workdir.is_symlink() or workdir.exists():
        raise ExampleError("--workdir must be a new directory, not an existing path")
    workdir = workdir.resolve()
    if workdir.is_relative_to(root):
        raise ExampleError("--workdir must be outside the checkout")
    workdir.mkdir(parents=True, mode=0o700)
    temporary = workdir / "tmp"
    temporary.mkdir(mode=0o700)
    progress_dir = workdir / "progress"
    progress_dir.mkdir(mode=0o700)
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith(("PYTHON", "GIT_"))
    }
    environment["TMPDIR"] = str(temporary)
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PYTHONSAFEPATH"] = "1"
    receipt: dict[str, Any] = {
        "schema": "ipe.documented-examples/1",
        "status": "failed",
        "started_at": datetime.now(UTC).isoformat(),
        "checkout": str(root),
        "harness_sha256": digest(Path(__file__).read_bytes()),
        "python": sys.version,
        "include_delegated": include_delegated,
        "execution_scope": "all_registered_blocks" if include_delegated else "quick_examples",
        "timeout_seconds": FULL_COMMAND_TIMEOUT if include_delegated else COMMAND_TIMEOUT,
        "syntax_checks": [],
        "executions": [],
        "completed_blocks": [],
        "delegated_blocks": [],
        "gpg_cleanup": [],
        "all_executable_examples_executed": False,
        "limits": [
            "Only completed_blocks identify fully executed source fences.",
            "In the default scope, delegated Bash recipes receive syntax checks only.",
            "JSON/YAML examples may be partial authoring fragments; they are not executed.",
            "Technical execution does not constitute independent human onboarding "
            "or pilot acceptance.",
        ],
    }
    before: dict[str, Any] | None = None
    copied_before: dict[str, Any] | None = None
    execution_root = root
    visible_source: dict[str, str] | None = None
    try:
        before = fingerprint(root)
        receipt["fixtures"] = {**before, "verified_unchanged": False}
        blocks = registered_blocks(root)
        receipt["blocks"] = [
            {"id": b.identifier, "line": b.line, "sha256": digest(b.body.encode("utf-8"))}
            for b in blocks
        ]
        receipt["documents"] = {name: before["files"][name] for name in BASH_POLICIES}
        for block in blocks:
            gate = (
                BASH_POLICIES[block.document][block.ordinal] if block.language == "bash" else None
            )
            if gate is not None:
                receipt["delegated_blocks"].append(
                    {"block": block.identifier, "gate": gate, "status": "not_executed_here"}
                )
        execution_root, copied_before, visible_source = configure_execution(
            root, workdir, environment, receipt, include_delegated=include_delegated
        )
        check_syntax(blocks, execution_root, environment, receipt)
        execute_examples(
            blocks,
            execution_root,
            environment,
            receipt,
            progress_dir,
            include_delegated=include_delegated,
            python_executable=str(execution_root / ".venv/bin/python")
            if copied_before is not None
            else sys.executable,
        )
        receipt["all_executable_examples_executed"] = set(receipt["completed_blocks"]) == {
            block.identifier for block in blocks
        }
        if include_delegated and not receipt["all_executable_examples_executed"]:
            raise ExampleError("Some executable examples were not completed")
        receipt["status"] = "passed"
    except (ExampleError, OSError, UnicodeError) as error:
        receipt["error_type"] = type(error).__name__
        receipt["error"] = str(error)
    finally:
        finalize_execution(
            root,
            execution_root,
            temporary,
            environment,
            receipt,
            before=before,
            copied_before=copied_before,
            visible_source=visible_source,
        )
        if digest(Path(__file__).read_bytes()) != receipt["harness_sha256"]:
            receipt["status"] = "failed"
            receipt["error"] = "Documented-example harness changed during execution"
        receipt["finished_at"] = datetime.now(UTC).isoformat()
        (workdir / "documented-examples.json").write_text(
            json.dumps(receipt, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workdir", required=True, type=Path, help="New external result directory")
    parser.add_argument(
        "--include-delegated",
        action="store_true",
        help="Execute every fence, including release/signing and the development test suite",
    )
    arguments = parser.parse_args(argv)
    try:
        receipt = run_examples(arguments.workdir, include_delegated=arguments.include_delegated)
    except (ExampleError, OSError) as error:
        print(f"Documented examples failed: {error}", file=sys.stderr)
        return 1
    if receipt["status"] != "passed":
        print(
            f"Documented examples failed: {receipt.get('error', 'check failed')}", file=sys.stderr
        )
        return 1
    print(
        f"Documented examples passed: {len(receipt['syntax_checks'])} syntax checks, "
        f"{len(receipt['executions'])} execution groups; "
        f"{len(receipt['completed_blocks'])} source blocks completed"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
