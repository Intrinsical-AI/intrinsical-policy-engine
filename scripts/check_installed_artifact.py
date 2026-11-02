# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Exercise an installed CLI outside its checkout using explicit starter fixtures."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tomllib
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

COMMANDS = {"lint", "assess", "export", "seal", "inspect"}
CHECKSUMS = "CHECKSUMS.sha256"
SENSITIVE_VALUE = "Installed acceptance reviewer <reviewer@example.test>"
EVIDENCE_PATH = Path("starter/control-review.md")
HEADER_SECRET = "artifact-header-secret-93617"
OFFLINE_HEADERS = ("Authorization", "X-Auth-Token", "Private-Token", "Content-Type", "X-Custom")


class AcceptanceError(RuntimeError):
    """The installed artifact did not satisfy its public contract."""


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AcceptanceError(message)


class Runner:
    def __init__(self, executable: Path, workdir: Path) -> None:
        self.executable = executable
        self.workdir = workdir
        self.records: list[dict[str, Any]] = []
        self.log_path = workdir / "logs" / "commands.jsonl"
        self.run_ids: set[str] = set()
        self.env = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
        self.env["PYTHONNOUSERSITE"] = "1"
        self.env["PYTHONSAFEPATH"] = "1"

    def run(
        self, label: str, *arguments: str | Path, success: bool = True, log: bool = False
    ) -> str:
        command = [str(self.executable), *(str(value) for value in arguments)]
        previous_log = b""
        if log:
            previous_log = self.log_path.read_bytes() if self.log_path.exists() else b""
            command.extend(("--log-jsonl", str(self.log_path)))
        result = subprocess.run(
            command,
            cwd=self.workdir,
            env=self.env,
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
        self.records.append(
            {
                "check": label,
                "command": command,
                "returncode": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )
        require(
            (result.returncode == 0) == success,
            f"{label}: unexpected exit {result.returncode}\n{result.stdout}\n{result.stderr}",
        )
        require(
            "Traceback (most recent call last)" not in result.stderr,
            f"{label}: operational failure exposed a traceback\n{result.stderr}",
        )
        require(
            all(
                secret not in result.stderr
                for secret in (SENSITIVE_VALUE, "artifact-test-secret", HEADER_SECRET)
            ),
            f"{label}: diagnostic exposed private input",
        )
        if log:
            self.check_log(previous_log, str(arguments[0]), result.returncode)
        print(f"passed: {label}")
        return result.stdout

    def check_log(self, previous: bytes, command: str, returncode: int) -> None:
        require(
            self.log_path.is_file() and not self.log_path.is_symlink(),
            "command did not create a regular operational log",
        )
        if os.name != "nt":
            require(
                stat.S_IMODE(self.log_path.stat().st_mode) == 0o600,
                "new operational log is not private (0600)",
            )
        content = self.log_path.read_bytes()
        require(content.startswith(previous), "command replaced existing log records")
        require(
            SENSITIVE_VALUE.encode() not in content and b"artifact-test-secret" not in content,
            "operational log exposed private input",
        )
        records: list[dict[str, Any]] = []
        for line in content[len(previous) :].decode("utf-8").splitlines():
            value: object = json.loads(line)
            if not isinstance(value, dict):
                raise AcceptanceError("operational log record is not an object")
            records.append(value)
        require(bool(records), "command emitted no operational log records")
        fields = {"timestamp", "run_id", "command", "level", "event", "data"}
        for record in records:
            require(set(record) == fields, "operational log has an unexpected schema")
            require(record["command"] == command, "log command differs from invocation")
            require(isinstance(record["data"], dict), "log data is not an object")
            for field in ("timestamp", "run_id", "level", "event"):
                require(
                    isinstance(record[field], str) and bool(record[field]),
                    f"log {field} is not nonempty text",
                )
            require(
                datetime.fromisoformat(record["timestamp"].replace("Z", "+00:00")).tzinfo
                is not None,
                "log timestamp has no timezone",
            )
        run_id = records[0]["run_id"]
        require(all(record["run_id"] == run_id for record in records), "run has multiple IDs")
        require(run_id not in self.run_ids, "operational run ID was reused")
        self.run_ids.add(run_id)
        lifecycle = [record for record in records if record["event"].startswith("command.")]
        terminal = "command.complete" if returncode == 0 else "command.failed"
        require(
            [record["event"] for record in lifecycle] == ["command.start", terminal],
            "command lifecycle has missing, duplicate, or inconsistent terminal records",
        )
        data = lifecycle[-1]["data"]
        require(
            type(data.get("exit_code")) is int and data["exit_code"] == returncode,
            "logged exit code differs from CLI result",
        )
        require(
            type(data.get("duration_ms")) is int and data["duration_ms"] >= 0,
            "logged duration is not a nonnegative integer",
        )
        if returncode:
            require(
                isinstance(data.get("error_type"), str) and bool(data["error_type"]),
                "failure log omitted its error category",
            )
        print(f"passed: {command} appended private lifecycle records")

    def reject(self, label: str, destination: Path, *arguments: str | Path) -> None:
        self.run(label, *arguments, success=False)
        require(not destination.exists(), f"{label}: failure published {destination.name}")


def json_object(path: Path) -> dict[str, Any]:
    value: object = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise AcceptanceError(f"{path.name}: expected a JSON object")
    return value


def file_fingerprint(path: Path) -> dict[str, Any]:
    require(path.is_file(), f"acceptance input is not a regular file: {path}")
    data = path.read_bytes()
    return {"path": str(path), "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}


def fixture_fingerprint(fixtures: Path) -> dict[str, Any]:
    """Fingerprint only the source fixtures consumed by this standalone harness."""
    files = [fixtures / "pyproject.toml"]
    for relative in ("frameworks/starter", "demos/starter/basic", "demos/starter/configs"):
        directory = fixtures / relative
        require(directory.is_dir(), f"missing acceptance fixture directory: {relative}")
        for path in directory.rglob("*"):
            require(not path.is_symlink(), f"symbolic link in acceptance fixtures: {path}")
            if path.is_file():
                files.append(path)
    manifest = {
        path.relative_to(fixtures).as_posix(): file_fingerprint(path)["sha256"]
        for path in sorted(files)
    }
    digest = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"root": str(fixtures), "sha256": digest, "files": manifest}


def python_metadata(runner: Runner) -> dict[str, str]:
    python = runner.executable.parent / "python"
    require(python.is_file(), "--ipe must have a sibling Python interpreter")
    result = subprocess.run(
        [
            str(python),
            "-I",
            "-c",
            "import json,sys; "
            "print(json.dumps({'version':sys.version,'executable':sys.executable}))",
        ],
        cwd=runner.workdir,
        env=runner.env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    require(result.returncode == 0, "could not identify the installed Python interpreter")
    value: object = json.loads(result.stdout)
    require(
        isinstance(value, dict) and all(isinstance(item, str) for item in value.values()),
        "installed Python returned invalid runtime metadata",
    )
    if not isinstance(value, dict):
        raise AcceptanceError("installed Python metadata must be an object")
    return value


def strings(value: Any):
    if isinstance(value, dict):
        for item in value.values():
            yield from strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from strings(item)
    elif isinstance(value, str):
        yield value


def check_metadata(root: Path, raw: dict[str, Any], *, include_raw: bool) -> None:
    summary = json_object(root / "_metadata/summary.json")
    trace = json_object(root / "_metadata/trace.json")
    require(summary["trace"] == trace, "summary and trace describe different assessments")
    require("STARTER-CONTROL-REVIEW" in summary["actions"], "starter action was not selected")
    require(summary["trace"]["plan_hash"] == trace["plan_hash"], "plan identity is inconsistent")
    for document in (summary, trace):
        for value in strings(document):
            require(
                not Path(value).is_absolute() and not re.match(r"^[A-Za-z]:[\\/]", value),
                f"{root.name}: internal absolute path entered assessment metadata",
            )
    answers = trace["answers_raw"]["input"]["answers"]
    if include_raw:
        require(answers == raw["answers"], "explicit raw trace lost original answer values")
        require(summary["system"]["name"] == SENSITIVE_VALUE, "explicit raw metadata was redacted")
    else:
        require(
            all(value == "[redacted]" for value in answers.values()),
            "default trace retained original answer values",
        )
        require(
            SENSITIVE_VALUE not in json.dumps(summary), "default summary leaked identity metadata"
        )
        require(SENSITIVE_VALUE not in json.dumps(trace), "default trace leaked identity metadata")
        require(summary["system"]["name"] == "[redacted]", "default identity was not redacted")


def inventory(root: Path) -> dict[str, bytes]:
    result = {}
    for path in root.rglob("*"):
        require(not path.is_symlink(), f"snapshot contains a link: {path.name}")
        if path.is_file():
            result[path.relative_to(root).as_posix()] = path.read_bytes()
    return result


def check_inventory(root: Path) -> dict[str, bytes]:
    files = inventory(root)
    require(CHECKSUMS in files, "root checksum inventory is missing")
    expected: dict[str, str] = {}
    for line in files[CHECKSUMS].decode("utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  (.+)", line)
        require(match is not None, "invalid checksum inventory line")
        if match is None:
            raise AcceptanceError("invalid checksum inventory line")
        digest, name = match.groups()
        require(
            name not in expected and not Path(name).is_absolute() and ".." not in Path(name).parts,
            "unsafe or duplicate inventory path",
        )
        expected[name] = digest
    payload = {name: data for name, data in files.items() if name != CHECKSUMS}
    require(set(expected) == set(payload), "checksum inventory omits or adds snapshot files")
    for name, data in payload.items():
        require(hashlib.sha256(data).hexdigest() == expected[name], f"checksum mismatch: {name}")
    require(not any(name.endswith(".asc") for name in files), "unsigned fixture gained a signature")
    return files


def check_zip(path: Path, expected: dict[str, bytes]) -> None:
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
        require(len(names) == len(set(names)), "sealed ZIP has duplicate members")
        require(set(names) == set(expected), "sealed ZIP omits or adds snapshot files")
        for name, data in expected.items():
            require(archive.read(name) == data, f"sealed ZIP changed bytes: {name}")


def check_api_payloads(root: Path) -> None:
    actions: list[str] = json_object(root / "_metadata/summary.json")["actions"]
    reference: dict[str, str] = {}
    for target in ("asana", "jira", "linear"):
        base = root / "exports" / target
        records: list[dict[str, Any]] = []
        for line in (base / "requests.ndjson").read_text(encoding="utf-8").splitlines():
            value: object = json.loads(line)
            if not isinstance(value, dict):
                raise AcceptanceError(f"{target}: request record must be an object")
            records.append(value)
        index = json_object(base / "index.json")
        require(
            index["target"] == target and index["count"] == len(actions),
            f"{target}: index does not describe the selected actions",
        )
        require(len(records) == len(actions), f"{target}: missing or duplicate action records")
        identifiers: dict[str, str] = {}
        for record in records:
            body = record["body"]
            if target == "asana":
                title = body["data"]["name"]
                require(
                    body["data"]["external"]["gid"] == record["uid"],
                    "Asana external identity differs from its envelope",
                )
            elif target == "jira":
                title = body["fields"]["summary"]
                properties = {entry["key"]: entry["value"] for entry in body["properties"]}
                require(
                    properties["externalId"] == record["uid"],
                    "Jira external identity differs from its envelope",
                )
            else:
                title = body["variables"]["input"]["title"]
                require(
                    "IssueCreateInput" in body["query"] and "issueCreate" in body["query"],
                    "Linear did not emit an issue creation mutation",
                )
                require(
                    all(key not in json.dumps(body) for key in ("clientMutationId", "externalId")),
                    "Linear includes unsupported issue input identifiers",
                )
            require(isinstance(title, str), f"{target}: action title is not text")
            matches = [action for action in actions if title.endswith(f"({action})")]
            require(len(matches) == 1, f"{target}: request cannot be matched to one action")
            action = matches[0]
            uid = record["uid"]
            if not isinstance(uid, str) or not uid:
                raise AcceptanceError(f"{target}: envelope UID is invalid")
            require(action not in identifiers, f"{target}: duplicate action UID")
            identifiers[action] = uid
            require(
                bool(record["headers"])
                and all(value == "<redacted>" for value in record["headers"].values()),
                f"{target}: configured header values were not withheld",
            )
            require(
                "artifact-test-secret" not in json.dumps(record)
                and HEADER_SECRET not in json.dumps(record)
                and SENSITIVE_VALUE not in json.dumps(record),
                f"{target}: request leaked input",
            )
        if reference:
            require(identifiers == reference, f"{target}: envelope UIDs differ by action")
        else:
            reference = identifiers
    with (root / "exports/asana/import.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    require(len(rows) == len(actions), "Asana CSV does not contain every selected action")
    csv_identifiers: dict[str, str] = {}
    for row in rows:
        matches = [action for action in actions if row["Name"].endswith(f"({action})")]
        require(
            len(matches) == 1 and matches[0] not in csv_identifiers,
            "Asana CSV has unknown or duplicate actions",
        )
        csv_identifiers[matches[0]] = row["External Id"]
    require(
        csv_identifiers == reference, "Asana CSV external IDs differ from request envelope UIDs"
    )
    print("passed: three offline API payloads and Asana CSV share action UIDs")


def check_installed_imports(runner: Runner) -> None:
    python = runner.executable.parent / "python"
    require(python.is_file(), "--ipe must be a CLI in an environment with a sibling python")
    code = """
import importlib.metadata
import importlib.util
import json
import pathlib
import sys
import intrinsical_policy_engine
from intrinsical_policy_engine.app.cli import main

removed = [
    'src', 'scripts',
    'intrinsical_policy_engine.adapters.ui',
    'intrinsical_policy_engine.adapters.store',
    'intrinsical_policy_engine.adapters.export.csv',
    'intrinsical_policy_engine.app.cli.wizard',
    'intrinsical_policy_engine.app.cli.commands.validate',
    'intrinsical_policy_engine.app.cli.commands.ops',
    'intrinsical_policy_engine.app.cli.commands.dev',
    'intrinsical_policy_engine.app.rendering.artifact_renderer',
    'intrinsical_policy_engine.domain.graph',
    'intrinsical_policy_engine.domain.simulation',
]
distribution = importlib.metadata.distribution('intrinsical-policy-engine')
typing_marker = pathlib.Path(intrinsical_policy_engine.__file__).with_name('py.typed')
print(json.dumps({
    'prefix': str(pathlib.Path(sys.prefix).resolve()),
    'modules': {
        name: str(pathlib.Path(module.__file__).resolve())
        for name, module in sorted(sys.modules.items())
        if name == 'intrinsical_policy_engine' or name.startswith('intrinsical_policy_engine.')
    },
    'retained_modules': [name for name in removed if importlib.util.find_spec(name) is not None],
    'entrypoints': [
        entry.value for entry in distribution.entry_points
        if entry.group == 'console_scripts' and entry.name == 'ipe'
    ],
    'typing_marker': {
        'path': str(typing_marker.resolve()),
        'regular_file': typing_marker.is_file() and not typing_marker.is_symlink(),
    },
}))
"""
    result = subprocess.run(
        [str(python), "-I", "-c", code],
        cwd=runner.workdir,
        env=runner.env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    require(
        result.returncode == 0,
        f"could not inspect the isolated installed runtime: {result.stderr}",
    )
    metadata: object = json.loads(result.stdout)
    require(isinstance(metadata, dict), "installed import metadata is not an object")
    if not isinstance(metadata, dict):
        raise AcceptanceError("installed import metadata must be an object")
    check_runtime_origins(metadata)
    print("passed: installed imports and removed surface")


def check_runtime_origins(metadata: dict[str, Any]) -> None:
    """Require installed typed packages without legacy modules or stale entry points."""
    prefix = Path(metadata["prefix"])
    modules = metadata["modules"]
    require(isinstance(modules, dict) and bool(modules), "installed runtime has no module origins")
    require(
        all(Path(path).is_relative_to(prefix) for path in modules.values()),
        "runtime imports did not originate from the installed environment "
        "(editable/source install)",
    )
    require(
        not metadata["retained_modules"],
        f"installed environment retained deleted modules: {metadata['retained_modules']}",
    )
    require(
        metadata["entrypoints"] == ["intrinsical_policy_engine.app.cli.main:main"],
        "installed ipe console entry point does not target the public package",
    )
    require(
        metadata.get("typing_marker")
        == {
            "path": str(Path(modules["intrinsical_policy_engine"]).with_name("py.typed")),
            "regular_file": True,
        },
        "installed package has no regular py.typed marker alongside its runtime",
    )


def check_invalid_api_configuration(
    runner: Runner, root: Path, pack: Path, answers: Path, settings: dict[str, Any]
) -> None:
    cases = (
        ("asana", "tag_gids", [""]),
        ("linear", "labelIds_by_label", {"P1": "   "}),
    )
    for target, field, value in cases:
        config = json.loads(json.dumps(settings))
        config[target][field] = value
        path = root / f"invalid-{target}-config.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        destination = root / f"invalid-{target}-output"
        runner.reject(
            f"invalid nested exporter ID rejected: {target}",
            destination,
            "export",
            "--contracts",
            pack,
            "--answers",
            answers,
            "--config",
            path,
            "--target",
            target,
            "--out",
            destination,
        )
    for kind, headers in (
        ("name", {"X-Invalid\r\nName": "public"}),
        ("value", {"X-Custom": HEADER_SECRET + "\r\nInjected: header"}),
    ):
        config = json.loads(json.dumps(settings))
        config["asana"]["headers"] = headers
        path = root / f"invalid-header-{kind}.json"
        path.write_text(json.dumps(config), encoding="utf-8")
        destination = root / f"invalid-header-{kind}-output"
        runner.reject(
            f"HTTP control characters rejected: {kind}",
            destination,
            "export",
            "--contracts",
            pack,
            "--answers",
            answers,
            "--config",
            path,
            "--target",
            "asana",
            "--out",
            destination,
        )


def check_calendar_boundaries(
    runner: Runner, root: Path, pack: Path, answers: Path, evidence: Path
) -> None:
    calendar_pack = root / "calendar-pack"
    shutil.copytree(pack, calendar_pack)
    calendar = calendar_pack / "law/policy/law_calendar.yml"
    calendar.write_text(json.dumps({"calendar": {"review": "T+1d"}}), encoding="utf-8")
    (calendar_pack / "law/policy/due_rules.yml").write_text(
        json.dumps(
            {
                "rules": [
                    {
                        "id": "acceptance-review-date",
                        "ids": ["STARTER-CONTROL-REVIEW"],
                        "calendar_keys": ["review"],
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    destination = root / "calendar-overflow-output"
    runner.reject(
        "relative calendar overflow rejected",
        destination,
        "assess",
        "--contracts",
        calendar_pack,
        "--answers",
        answers,
        "--base-date",
        "9999-12-31",
        "--out",
        destination,
    )
    calendar.write_text(json.dumps({"calendar": {"review": "9999-12-31"}}), encoding="utf-8")
    destination = root / "maximum-calendar-output"
    runner.run(
        "maximum calendar date exported",
        "export",
        "--contracts",
        calendar_pack,
        "--answers",
        answers,
        "--evidence-dir",
        evidence,
        "--check-ci",
        "--strict",
        "--out",
        destination,
    )
    content = (destination / "compliance.ics").read_bytes()
    require(
        b"DTSTART;VALUE=DATE:99991231\r\n" in content and b"DURATION:P1D\r\n" in content,
        "maximum calendar date was dropped or given an overflowing end date",
    )


def check_rejected_evidence(runner: Runner, root: Path, pack: Path, answers: Path) -> None:
    csv_pack = root / "csv-pack"
    shutil.copytree(pack, csv_pack)
    relative = "starter/control-review.csv"
    for name in ("law/core/actions.yml", "law/policy/evidence_map.yml"):
        path = csv_pack / name
        text = path.read_text(encoding="utf-8")
        require(EVIDENCE_PATH.as_posix() in text, f"starter requirement missing in {name}")
        path.write_text(text.replace(EVIDENCE_PATH.as_posix(), relative), encoding="utf-8")
    evidence = root / "regression-evidence"
    path = evidence / relative
    path.parent.mkdir(parents=True)
    path.write_text("review,result\n" + "control,complete\n" * 5, encoding="utf-8")
    common = (
        "--contracts",
        csv_pack,
        "--answers",
        answers,
        "--evidence-dir",
        evidence,
        "--check-ci",
        "--strict",
    )
    runner.run(
        "CSV evidence fixture accepted", "export", *common, "--out", root / "valid-csv-output"
    )
    for label, stem, content in (
        (
            "malformed CSV evidence rejected",
            "malformed-csv",
            "review,result\n" + '"control"junk,complete\n' * 5,
        ),
        ("empty CSV header rejected", "empty-csv-header", ",\n" + "control,complete\n" * 5),
        ("empty CSV rows rejected", "empty-csv-rows", "review,result\n" + " , \t\n" * 5),
    ):
        path.write_text(content, encoding="utf-8")
        destination = root / f"{stem}-output"
        runner.reject(label, destination, "export", *common, "--out", destination)
    markdown = evidence / EVIDENCE_PATH
    markdown.write_text(
        '---\ntitle: completed-review\n## Completed review\ndescription: "'
        + "Completed assessed controls reviewed and results documented. " * 20
        + '"\n---',
        encoding="utf-8",
    )
    destination = root / "metadata-only-output"
    runner.reject(
        "front matter ending at EOF rejected",
        destination,
        "export",
        "--contracts",
        pack,
        "--answers",
        answers,
        "--evidence-dir",
        evidence,
        "--check-ci",
        "--strict",
        "--out",
        destination,
    )


def run_regression_acceptance(runner: Runner, fixtures: Path) -> None:
    """Exercise the confirmed failures through the installed executable only."""
    root = runner.workdir / "regressions"
    root.mkdir()
    pack = root / "pack"
    inputs = root / "inputs"
    shutil.copytree(fixtures / "frameworks/starter", pack)
    shutil.copytree(fixtures / "demos/starter/basic", inputs)
    answers = inputs / "answers.json"
    evidence = inputs / "evidence"
    settings = json_object(fixtures / "demos/starter/configs/offline-all.json")
    for target in ("asana", "jira", "linear"):
        settings[target]["headers"] = dict.fromkeys(OFFLINE_HEADERS, HEADER_SECRET)
    config = root / "headers-config.json"
    config.write_text(json.dumps(settings), encoding="utf-8")
    output = root / "header-export"
    runner.run(
        "arbitrary offline headers withheld",
        "export",
        "--contracts",
        pack,
        "--answers",
        answers,
        "--evidence-dir",
        evidence,
        "--check-ci",
        "--strict",
        "--config",
        config,
        "--target",
        "asana",
        "jira",
        "linear",
        "--out",
        output,
    )
    check_api_payloads(output)
    for target in ("asana", "jira", "linear"):
        requests = (output / "exports" / target / "requests.ndjson").read_text(encoding="utf-8")
        require(
            all(
                json.loads(line)["headers"] == dict.fromkeys(OFFLINE_HEADERS, "<redacted>")
                for line in requests.splitlines()
            ),
            f"{target}: arbitrary headers were omitted or retained their values",
        )
    check_invalid_api_configuration(runner, root, pack, answers, settings)
    check_calendar_boundaries(runner, root, pack, answers, evidence)
    check_rejected_evidence(runner, root, pack, answers)
    log = root / "existing-log.jsonl"
    previous = b'{"event":"prior"}'
    log.write_bytes(previous)
    runner.run(
        "unterminated log line separated",
        "lint",
        "--contracts",
        pack,
        "--strict",
        "--log-jsonl",
        log,
    )
    content = log.read_bytes()
    require(
        content.startswith(previous + b"\n"), "append concatenated the prior and new JSON records"
    )
    records = [json.loads(line) for line in content.splitlines()]
    require(
        [record["event"] for record in records] == ["prior", "command.start", "command.complete"],
        "unterminated prior log lost records or command lifecycle",
    )


def run_acceptance(runner: Runner, fixtures: Path) -> None:
    work = runner.workdir
    pack = work / "pack"
    inputs = work / "inputs"
    shutil.copytree(fixtures / "frameworks/starter", pack)
    shutil.copytree(fixtures / "demos/starter/basic", inputs)
    project = tomllib.loads((fixtures / "pyproject.toml").read_text(encoding="utf-8"))
    version = runner.run("version", "--version").strip()
    require(
        version == f"ipe {project['project']['version']}", "installed version differs from source"
    )
    help_text = runner.run("five commands", "--help")
    names = set(re.findall(r"^    ([a-z][a-z-]+)\s+", help_text, flags=re.MULTILINE))
    require(names == COMMANDS, f"unexpected CLI command surface: {sorted(names)}")
    for command in sorted(COMMANDS):
        command_help = runner.run(f"{command} help", command, "--help")
        require("--log-jsonl" in command_help, f"{command}: help omitted operational logging")
        if command == "export":
            require(
                "--target" in command_help
                and all(
                    re.search(rf"\b{target}\b", command_help)
                    for target in ("filesystem", "asana", "jira", "linear")
                ),
                "export help omitted the four explicit target choices",
            )
    check_installed_imports(runner)
    runner.run("lint", "lint", "--contracts", pack, "--strict", log=True)
    overview: object = json.loads(
        runner.run("inspect pack overview", "inspect", "--contracts", pack, log=True)
    )
    if not isinstance(overview, dict):
        raise AcceptanceError("inspect overview is not an object")
    require(
        overview.get("framework_id") == "starter"
        and isinstance(overview.get("requires_engine"), str)
        and isinstance(overview.get("flags"), list)
        and isinstance(overview.get("questions"), list)
        and isinstance(overview.get("evidence"), dict)
        and overview.get("action_evidence", {}).get("STARTER-CONTROL-REVIEW")
        == [EVIDENCE_PATH.as_posix()],
        "inspect overview lost input identifiers or evidence requirements",
    )
    inspected = runner.run(
        "inspect", "inspect", "--contracts", pack, "rule", "STARTER-DERIVE-REVIEW-FROM-AUTOMATION"
    )
    require(
        json.loads(inspected)["id"] == "STARTER-DERIVE-REVIEW-FROM-AUTOMATION",
        "inspect did not return the selected compiled rule",
    )
    raw = json_object(inputs / "answers.json")
    raw["system"] = {"name": SENSITIVE_VALUE}
    raw["provider"] = {"contact": SENSITIVE_VALUE}
    raw["meta"] = {"review_note": SENSITIVE_VALUE}
    answers = inputs / "acceptance-answers.json"
    answers.write_text(json.dumps(raw), encoding="utf-8")
    common = ("--contracts", pack, "--answers", answers, "--strict")
    assessment = work / "assessment"
    runner.run("assess redacted", "assess", *common, "--out", assessment, log=True)
    check_metadata(assessment, raw, include_raw=False)
    check_inventory(assessment)
    raw_assessment = work / "assessment-raw"
    runner.run(
        "assess raw opt-in", "assess", *common, "--include-raw-input", "--out", raw_assessment
    )
    check_metadata(raw_assessment, raw, include_raw=True)
    invalid_input = inputs / "invalid-input.json"
    invalid_input.write_text(json.dumps({"answers": raw["answers"], "flags": []}), encoding="utf-8")
    destination = work / "invalid-input-output"
    runner.reject(
        "ambiguous input rejected",
        destination,
        "assess",
        "--contracts",
        pack,
        "--answers",
        invalid_input,
        "--out",
        destination,
    )
    invalid_values = inputs / "invalid-values.json"
    invalid_values.write_text(
        json.dumps({"answers": {"STARTER_Q1": {"private": SENSITIVE_VALUE}}}), encoding="utf-8"
    )
    destination = work / "invalid-values-output"
    runner.run(
        "invalid answer type rejected",
        "assess",
        "--contracts",
        pack,
        "--answers",
        invalid_values,
        "--out",
        destination,
        success=False,
        log=True,
    )
    require(not destination.exists(), "invalid answer type published an output")
    for suffix, content in (
        ("yaml", "flags: []\nmeta:\n  nested:\n    - measure: .nan\n"),
        ("json", '{"flags":[],"meta":{"nested":[{"measure":1e1000}]}}'),
    ):
        nonfinite_input = inputs / f"nonfinite.{suffix}"
        nonfinite_input.write_text(content, encoding="utf-8")
        for command in ("assess", "export"):
            destination = work / f"nonfinite-{suffix}-{command}-output"
            runner.reject(
                f"{command} rejects {suffix} nonfinite metadata with raw opt-in",
                destination,
                command,
                "--contracts",
                pack,
                "--answers",
                nonfinite_input,
                "--include-raw-input",
                "--out",
                destination,
            )
    destination = work / "invalid-target-output"
    runner.reject(
        "invalid target rejected",
        destination,
        "export",
        *common,
        "--target",
        "unknown-target",
        "--out",
        destination,
    )
    occupied = work / "occupied"
    occupied.mkdir()
    sentinel = occupied / "keep.txt"
    sentinel.write_text("retain existing content", encoding="utf-8")
    runner.run("occupied output rejected", "export", *common, "--out", occupied, success=False)
    require(
        sentinel.read_text() == "retain existing content" and set(occupied.iterdir()) == {sentinel},
        "existing output was modified",
    )
    for command in ("assess", "export"):
        empty_output = work / f"empty-{command}-output"
        empty_output.mkdir()
        runner.run(
            f"{command} empty existing output rejected",
            command,
            *common,
            "--out",
            empty_output,
            success=False,
        )
        require(
            empty_output.is_dir() and not any(empty_output.iterdir()),
            f"{command}: existing empty output was modified",
        )
    destination = work / "no-evidence-output"
    runner.reject(
        "CI requires explicit evidence",
        destination,
        "export",
        *common,
        "--check-ci",
        "--out",
        destination,
    )
    false_evidence = work / "false-evidence"
    shutil.copytree(inputs / "evidence", false_evidence)
    (false_evidence / EVIDENCE_PATH).write_text(
        "---\nstatus: ready\n---\n\n# Review\nClaimed ready.\n", encoding="utf-8"
    )
    destination = work / "false-evidence-output"
    runner.reject(
        "declared ready cannot replace evidence",
        destination,
        "export",
        *common,
        "--check-ci",
        "--evidence-dir",
        false_evidence,
        "--out",
        destination,
    )
    exported = work / "export"
    evidence = inputs / "evidence"
    target_config = inputs / "targets.json"
    settings = json_object(fixtures / "demos/starter/configs/offline-all.json")
    for target in ("asana", "jira", "linear"):
        settings[target]["headers"] = {"Authorization": "Bearer artifact-test-secret"}
    target_config.write_text(json.dumps(settings), encoding="utf-8")
    runner.run(
        "export filesystem and three offline API targets with fixture evidence",
        "export",
        *common,
        "--target",
        "filesystem",
        "asana",
        "jira",
        "linear",
        "--config",
        target_config,
        "--check-ci",
        "--evidence-dir",
        evidence,
        "--out",
        exported,
        log=True,
    )
    check_metadata(exported, raw, include_raw=False)
    check_api_payloads(exported)
    require(
        (exported / "evidence" / EVIDENCE_PATH).read_bytes()
        == (evidence / EVIDENCE_PATH).read_bytes(),
        "evidence snapshot changed supplied bytes",
    )
    manifest = json_object(exported / "_metadata/evidence_manifest.json")
    require(
        EVIDENCE_PATH.as_posix() in manifest["included"] and not manifest["missing"],
        "evidence manifest does not describe the accepted document",
    )
    with zipfile.ZipFile(exported / "evidence.zip") as evidence_archive:
        require(
            evidence_archive.read(EVIDENCE_PATH.as_posix())
            == (evidence / EVIDENCE_PATH).read_bytes(),
            "evidence ZIP changed supplied bytes",
        )
    before = check_inventory(exported)
    snapshot_archive = work / "snapshot.zip"
    runner.run(
        "seal unsigned complete ZIP",
        "seal",
        "--export-dir",
        exported,
        "--output",
        snapshot_archive,
        "--strict",
        "--no-sign",
        log=True,
    )
    require(inventory(exported) == before, "unsigned sealing modified the source snapshot")
    check_zip(snapshot_archive, before)
    raw_export = work / "export-raw"
    runner.run(
        "export raw opt-in",
        "export",
        *common,
        "--include-raw-input",
        "--check-ci",
        "--evidence-dir",
        evidence,
        "--out",
        raw_export,
    )
    check_metadata(raw_export, raw, include_raw=True)
    check_inventory(raw_export)
    drift = work / "drift"
    shutil.copytree(exported, drift)
    summary = drift / "_metadata/summary.json"
    summary.write_bytes(summary.read_bytes() + b" \n")
    baseline = (drift / CHECKSUMS).read_bytes()
    for strict in (False, True):
        destination = work / ("drift-strict.zip" if strict else "drift.zip")
        options = ("--strict",) if strict else ()
        runner.reject(
            "baseline drift rejected" + (" strict" if strict else ""),
            destination,
            "seal",
            "--export-dir",
            drift,
            "--output",
            destination,
            "--no-sign",
            *options,
        )
        require((drift / CHECKSUMS).read_bytes() == baseline, "sealing rebased a drifted baseline")
    run_regression_acceptance(runner, fixtures)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--ipe", required=True, type=Path, help="Installed environment's ipe executable"
    )
    parser.add_argument(
        "--fixtures",
        required=True,
        type=Path,
        help="Fixture checkout root containing pyproject, frameworks/starter and demos",
    )
    parser.add_argument(
        "--workdir",
        required=True,
        type=Path,
        help="External harness directory (absent or empty; CLI output paths must be absent)",
    )
    parser.add_argument(
        "--artifact",
        type=Path,
        help="Optional wheel/sdist to hash before acceptance and recheck afterward",
    )
    args = parser.parse_args()
    runner = None
    metadata: dict[str, Any] = {
        "schema": "ipe.installed-acceptance/1",
        "schema_version": 1,
        "status": "failed",
    }
    exit_code = 1
    try:
        require(not args.workdir.is_symlink(), "workdir must not be a symbolic link")
        workdir = args.workdir.resolve()
        fixtures = args.fixtures.resolve(strict=True)
        executable = args.ipe.absolute()
        require(executable.is_file() and os.access(executable, os.X_OK), "--ipe is not executable")
        require(
            not workdir.is_relative_to(fixtures), "workdir must be outside the fixture checkout"
        )
        require(
            not workdir.exists() or (workdir.is_dir() and not any(workdir.iterdir())),
            "workdir must be new or empty",
        )
        workdir.mkdir(parents=True, exist_ok=True)
        runner = Runner(executable, workdir)
        metadata["ipe"] = {"path": str(executable)}
        metadata["fixtures"] = fixture_fingerprint(fixtures)
        metadata["fixtures"]["verified_unchanged"] = False
        metadata["artifact"] = (
            file_fingerprint(args.artifact.resolve(strict=True))
            if args.artifact is not None
            else None
        )
        metadata["artifact_verified_unchanged"] = None if args.artifact is None else False
        metadata["python"] = python_metadata(runner)
        metadata["harness"] = file_fingerprint(Path(__file__).resolve())
        run_acceptance(runner, fixtures)
        metadata["fixtures"]["verified_unchanged"] = (
            fixture_fingerprint(fixtures)["sha256"] == metadata["fixtures"]["sha256"]
        )
        if args.artifact is not None:
            metadata["artifact_verified_unchanged"] = (
                file_fingerprint(Path(metadata["artifact"]["path"])) == metadata["artifact"]
            )
        require(metadata["fixtures"]["verified_unchanged"], "fixtures changed during acceptance")
        require(
            metadata["artifact_verified_unchanged"] is not False,
            "artifact changed during acceptance",
        )
        metadata["status"] = "passed"
        exit_code = 0
    except (
        AcceptanceError,
        OSError,
        ValueError,
        KeyError,
        subprocess.TimeoutExpired,
        zipfile.BadZipFile,
    ) as exc:
        print(f"installed artifact acceptance failed: {exc}", file=sys.stderr)
        metadata["error_type"] = type(exc).__name__
    finally:
        if runner is not None:
            (runner.workdir / "acceptance.json").write_text(
                json.dumps(runner.records, indent=2), encoding="utf-8"
            )
            metadata["checks"] = {
                "count": len(runner.records),
                "names": [record["check"] for record in runner.records],
            }
            version = next(
                (
                    record["stdout"].strip()
                    for record in runner.records
                    if record["check"] == "version"
                ),
                None,
            )
            if version is not None and "ipe" in metadata:
                metadata["ipe"]["version"] = version
            (runner.workdir / "acceptance-metadata.json").write_text(
                json.dumps(metadata, indent=2), encoding="utf-8"
            )
    if exit_code == 0:
        print("installed artifact acceptance passed")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
