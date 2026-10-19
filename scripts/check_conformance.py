# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Run independently authored process-mock scenarios through an external CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING or __package__:
    from . import check_installed_artifact as acceptance
else:
    # Resolve only this trusted sibling directory under Python safe-path mode.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import check_installed_artifact as acceptance

DEMO = Path("demos/process-review")
CONCLUSION_FIELDS = (
    "flags",
    "actions",
    "outcome",
    "risk_tier",
    "outcome_axes",
    "routing",
    "calendar",
    "due_hints",
    "actions_evidence_map",
    "articles_evidence_map",
    "articles_overlay",
)


def canonical_json(value: object) -> str:
    """Keep JSON booleans distinct from numbers when comparing conclusions."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def conclusions(plan: dict[str, Any]) -> dict[str, Any]:
    """Project observable semantics, excluding execution-specific metadata."""
    result = {key: plan[key] for key in CONCLUSION_FIELDS}
    trace = plan["trace"]
    result.update(
        emitted_flags=trace["flags"]["emitted"],
        derived_flags=trace["flags"]["derived"],
        derivation_ids=[rule["id"] for rule in trace["derivations_applied"]],
        stop=trace["rules_applied"]["stops"],
    )
    return result


def fixture_fingerprint(fixtures: Path) -> dict[str, Any]:
    root = fixtures / DEMO
    acceptance.require(root.is_dir(), f"missing conformance fixture directory: {DEMO}")
    files = [fixtures / "pyproject.toml"]
    for path in root.rglob("*"):
        acceptance.require(not path.is_symlink(), f"symbolic link in conformance fixtures: {path}")
        if path.is_file():
            files.append(path)
    manifest = {
        path.relative_to(fixtures).as_posix(): acceptance.file_fingerprint(path)["sha256"]
        for path in sorted(files)
    }
    digest = hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {"root": str(fixtures), "sha256": digest, "files": manifest}


def fixture_path(root: Path, relative: object) -> Path:
    acceptance.require(
        isinstance(relative, str) and bool(relative), "scenario fixture path is not text"
    )
    if not isinstance(relative, str):
        raise acceptance.AcceptanceError("scenario fixture path must be text")
    path = Path(relative)
    acceptance.require(
        not path.is_absolute() and ".." not in path.parts and "\\" not in relative,
        f"unsafe scenario fixture path: {relative}",
    )
    return root / path


def load_scenarios(demo: Path) -> list[dict[str, Any]]:
    value: object = json.loads((demo / "expected/scenarios.json").read_text(encoding="utf-8"))
    acceptance.require(
        isinstance(value, list) and bool(value), "scenario corpus must be a nonempty list"
    )
    if not isinstance(value, list):
        raise acceptance.AcceptanceError("scenario corpus must be a list")
    identifiers: set[str] = set()
    for scenario in value:
        acceptance.require(isinstance(scenario, dict), "scenario must be an object")
        identifier = scenario.get("id")
        acceptance.require(
            isinstance(identifier, str)
            and re.fullmatch(r"[a-z0-9][a-z0-9-]*", identifier) is not None,
            "scenario id must be a safe directory name",
        )
        acceptance.require(identifier not in identifiers, f"duplicate scenario id: {identifier}")
        identifiers.add(identifier)
        acceptance.require(
            ("error" in scenario) != ("expected" in scenario),
            f"{identifier}: scenario needs either expected conclusions or an assessment error",
        )
        for key in ("error", "export_error"):
            if key in scenario:
                acceptance.require(
                    isinstance(scenario[key], str) and bool(scenario[key].strip()),
                    f"{identifier}: expected diagnostic must be nonempty text",
                )
        if "expected" in scenario:
            acceptance.require(
                isinstance(scenario["expected"], dict),
                f"{identifier}: expected conclusions must be an object",
            )
        source = fixture_path(demo, scenario.get("input_file"))
        acceptance.require(
            canonical_json(acceptance.json_object(source)) == canonical_json(scenario.get("input")),
            f"{identifier}: input file differs from the independent scenario",
        )
    return value


def check_plan(path: Path, expected: dict[str, Any], label: str) -> dict[str, Any]:
    plan = acceptance.json_object(path / "_metadata/summary.json")
    actual = conclusions(plan)
    differences = sorted(
        key
        for key in actual.keys() | expected.keys()
        if key not in actual
        or key not in expected
        or canonical_json(actual[key]) != canonical_json(expected[key])
    )
    acceptance.require(
        canonical_json(actual) == canonical_json(expected),
        f"{label}: semantic conclusions differ in {differences}",
    )
    acceptance.require(
        canonical_json(plan["trace"])
        == canonical_json(acceptance.json_object(path / "_metadata/trace.json")),
        f"{label}: summary and trace disagree",
    )
    acceptance.check_inventory(path)
    return plan


def reject_with_message(
    runner: acceptance.Runner, label: str, destination: Path, message: str, *arguments: str | Path
) -> None:
    runner.reject(label, destination, *arguments)
    acceptance.require(
        message in runner.records[-1]["stderr"], f"{label}: expected diagnostic {message!r}"
    )


def check_export(
    runner: acceptance.Runner,
    demo: Path,
    scenario: dict[str, Any],
    common: list[str | Path],
    root: Path,
) -> None:
    identifier = scenario["id"]
    expected = scenario["expected"]
    destination = root / "export"
    source = fixture_path(demo, scenario["evidence"])
    arguments = [
        "export",
        *common,
        "--evidence-dir",
        source,
        "--check-ci",
        "--strict",
        "--out",
        destination,
    ]
    if "export_error" in scenario:
        reject_with_message(
            runner,
            f"{identifier}: export rejected",
            destination,
            scenario["export_error"],
            *arguments,
        )
        return
    runner.run(f"{identifier}: export", *arguments)
    check_plan(destination, expected, identifier)
    readme = (destination / "README.md").read_text(encoding="utf-8")
    acceptance.require(
        "EU AI Act process mock" in readme and "no legal fidelity" in readme,
        f"{identifier}: educational scope disappeared from the output",
    )
    evidence = sorted(
        {name for paths in expected["actions_evidence_map"].values() for name in paths}
    )
    manifest = acceptance.json_object(destination / "_metadata/evidence_manifest.json")
    acceptance.require(manifest["included"] == evidence, f"{identifier}: evidence manifest differs")
    for relative in evidence:
        acceptance.require(
            fixture_path(destination / "evidence", relative).read_bytes()
            == fixture_path(source, relative).read_bytes(),
            f"{identifier}: supplied evidence bytes changed: {relative}",
        )
    calendar = destination / "compliance.ics"
    acceptance.require(
        calendar.exists() == bool(expected["due_hints"]), f"{identifier}: unexpected calendar"
    )
    if calendar.exists():
        content = calendar.read_text(encoding="utf-8")
        for deadline in expected["due_hints"].values():
            acceptance.require(
                deadline.replace("-", "") in content, f"{identifier}: calendar lost a deadline"
            )
    before = acceptance.inventory(destination)
    archive = root / "snapshot.zip"
    runner.run(
        f"{identifier}: seal",
        "seal",
        "--export-dir",
        destination,
        "--output",
        archive,
        "--strict",
        "--no-sign",
    )
    acceptance.require(
        acceptance.inventory(destination) == before,
        f"{identifier}: unsigned sealing changed the snapshot",
    )
    acceptance.check_zip(archive, before)


def run_scenario(runner: acceptance.Runner, demo: Path, scenario: dict[str, Any]) -> None:
    identifier = scenario["id"]
    root = runner.workdir / "scenarios" / identifier
    root.mkdir(parents=True)
    common: list[str | Path] = [
        "--contracts",
        demo / "pack",
        "--answers",
        fixture_path(demo, scenario["input_file"]),
    ]
    if "base_date" in scenario:
        common.extend(["--base-date", scenario["base_date"]])
    assessment = root / "assessment"
    if "error" in scenario:
        for command in ("assess", "export"):
            destination = root / command
            reject_with_message(
                runner,
                f"{identifier}: {command} rejected",
                destination,
                scenario["error"],
                command,
                *common,
                "--out",
                destination,
            )
        return
    runner.run(f"{identifier}: assess", "assess", *common, "--out", assessment)
    plan = check_plan(assessment, scenario["expected"], identifier)
    if "equivalent_input" in scenario:
        equivalent = root / "equivalent"
        equivalent_args: list[str | Path] = [
            "--contracts",
            demo / "pack",
            "--answers",
            fixture_path(demo, scenario["equivalent_input"]),
        ]
        if "base_date" in scenario:
            equivalent_args.extend(["--base-date", scenario["base_date"]])
        runner.run(
            f"{identifier}: equivalent flags", "assess", *equivalent_args, "--out", equivalent
        )
        other = check_plan(equivalent, scenario["expected"], f"{identifier} equivalent")
        acceptance.require(
            plan["trace"]["plan_hash"] == other["trace"]["plan_hash"],
            f"{identifier}: equivalent answers and flags have different semantic identities",
        )
    check_export(runner, demo, scenario, common, root)


def run_conformance(runner: acceptance.Runner, fixtures: Path) -> int:
    demo = runner.workdir / "relocated-process-review"
    shutil.copytree(fixtures / DEMO, demo)
    before = acceptance.inventory(demo)
    scenarios = load_scenarios(demo)
    runner.run("relocated mock lint", "lint", "--contracts", demo / "pack", "--strict")
    check_inspection(runner, demo, scenarios)
    for scenario in scenarios:
        run_scenario(runner, demo, scenario)
    acceptance.require(
        acceptance.inventory(demo) == before,
        "CLI changed the relocated source pack, inputs or expected scenarios",
    )
    return len(scenarios)


def check_inspection(
    runner: acceptance.Runner, demo: Path, scenarios: list[dict[str, Any]]
) -> None:
    expected = acceptance.json_object(demo / "expected/inspection.json")
    overview = json.loads(
        runner.run("inspect mock overview", "inspect", "--contracts", demo / "pack")
    )
    catalog = {
        family: [record["id"] for record in records]
        for family, records in expected["rules"].items()
    }
    for field, value in (
        ("framework_id", expected["framework_id"]),
        ("calendar", expected["calendar"]),
        ("rules", catalog),
    ):
        acceptance.require(
            canonical_json(overview[field]) == canonical_json(value),
            f"inspect mock overview differs in {field}",
        )
    for family, records in expected["rules"].items():
        for record in records:
            inspected = json.loads(
                runner.run(
                    f"inspect {family} {record['id']}",
                    "inspect",
                    "--contracts",
                    demo / "pack",
                    "rule",
                    family,
                    record["id"],
                )
            )
            acceptance.require(
                canonical_json(inspected) == canonical_json(record),
                f"inspect differs from independent {family} record {record['id']}",
            )
    for scenario in scenarios:
        if "expected" not in scenario:
            continue
        conclusions = scenario["expected"]
        references = {
            "derivation": conclusions["derivation_ids"],
            "stop": [conclusions["stop"]["stop_id"]] if conclusions["stop"] else [],
            "routing": [conclusions["routing"]["rule_id"]]
            if conclusions["routing"]["rule_id"]
            else [],
        }
        for family, identifiers in references.items():
            acceptance.require(
                set(identifiers) <= set(catalog[family]),
                f"{scenario['id']}: expected trace references an uninspected {family} rule",
            )
        for action, paths in conclusions["actions_evidence_map"].items():
            acceptance.require(
                overview["action_evidence"].get(action) == paths,
                f"{scenario['id']}: inspection and expected action evidence differ",
            )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--ipe", required=True, type=Path, help="External ipe executable")
    parser.add_argument("--fixtures", required=True, type=Path, help="Fixture checkout root")
    parser.add_argument(
        "--workdir", required=True, type=Path, help="New or empty external directory"
    )
    parser.add_argument(
        "--artifact", type=Path, help="Optional wheel/sdist to fingerprint and recheck afterward"
    )
    args = parser.parse_args()
    runner = None
    receipt: dict[str, Any] = {
        "schema": "ipe.conformance/1",
        "schema_version": 1,
        "status": "failed",
    }
    exit_code = 1
    try:
        acceptance.require(not args.workdir.is_symlink(), "workdir must not be a symbolic link")
        workdir = args.workdir.resolve()
        fixtures = args.fixtures.resolve(strict=True)
        executable = args.ipe.absolute()
        acceptance.require(
            executable.is_file() and os.access(executable, os.X_OK), "--ipe is not executable"
        )
        acceptance.require(
            not workdir.is_relative_to(fixtures), "workdir must be outside the fixture checkout"
        )
        acceptance.require(
            not workdir.exists() or (workdir.is_dir() and not any(workdir.iterdir())),
            "workdir must be new or empty",
        )
        workdir.mkdir(parents=True, exist_ok=True)
        runner = acceptance.Runner(executable, workdir)
        receipt["ipe"] = acceptance.file_fingerprint(executable)
        receipt["python"] = acceptance.python_metadata(runner)
        receipt["harness"] = acceptance.file_fingerprint(Path(__file__).resolve())
        receipt["artifact"] = (
            acceptance.file_fingerprint(args.artifact.resolve(strict=True))
            if args.artifact is not None
            else None
        )
        receipt["artifact_verified_unchanged"] = None if args.artifact is None else False
        receipt["fixtures"] = fixture_fingerprint(fixtures)
        receipt["fixtures"]["verified_unchanged"] = False
        receipt["scenarios"] = run_conformance(runner, fixtures)
        receipt["fixtures"]["verified_unchanged"] = (
            fixture_fingerprint(fixtures)["sha256"] == receipt["fixtures"]["sha256"]
        )
        acceptance.require(
            receipt["fixtures"]["verified_unchanged"], "fixtures changed during conformance"
        )
        if args.artifact is not None:
            receipt["artifact_verified_unchanged"] = (
                acceptance.file_fingerprint(Path(receipt["artifact"]["path"]))
                == receipt["artifact"]
            )
        acceptance.require(
            receipt["artifact_verified_unchanged"] is not False,
            "artifact changed during conformance",
        )
        receipt["status"] = "passed"
        exit_code = 0
    except (
        acceptance.AcceptanceError,
        OSError,
        ValueError,
        KeyError,
        TypeError,
        subprocess.TimeoutExpired,
        zipfile.BadZipFile,
    ) as exc:
        print(f"CLI conformance failed: {exc}", file=sys.stderr)
        receipt["error_type"] = type(exc).__name__
    finally:
        if runner is not None:
            receipt["checks"] = runner.records
            (runner.workdir / "conformance.json").write_text(
                json.dumps(receipt, indent=2), encoding="utf-8"
            )
    if exit_code == 0:
        print(f"CLI conformance passed: {receipt['scenarios']} independent scenarios")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
