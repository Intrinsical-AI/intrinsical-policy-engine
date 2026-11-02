# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Measure bounded synthetic assessment and evidence workloads, without network IO.

Each action has a flag derivation, an article and one ready JSON evidence file.
Inputs are reproducible; observed timings are machine-specific measurements, not
capacity guarantees or pass/fail budgets. Setup is excluded from measurements.

An optional --baseline report requires an explicit --max-slowdown fraction:
0.25 permits up to 1.25 times each baseline median elapsed time. Comparison
requires matching inputs, measurement method, script, Python, platform and dependencies.
Python allocation peaks remain observations and are not gated. A comparable
regression writes its receipt and exits 1; invalid baselines exit 2 without a
receipt. Existing output files are never overwritten.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import platform
import statistics
import sys
import tempfile
import time
import tracemalloc
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from typing import Any, TypeVar

import intrinsical_policy_engine
from intrinsical_policy_engine.app.evidence import EvidenceSnapshot, check_ci, collect_evidence
from intrinsical_policy_engine.common.constants import CANONICAL_ENGINE_VERSION
from intrinsical_policy_engine.domain.contract_models import ContractBundle
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle
from intrinsical_policy_engine.domain.services.integrity import compute_bundle_hash

ResultT = TypeVar("ResultT")
MAX_ACTIONS = 1_000
MAX_REPEATS = 20
TIMED_PHASES = ("assessment", "evidence_collection_and_ci")
COMPARABLE_ENVIRONMENT = (
    "python_version",
    "python_implementation",
    "python_compiler",
    "platform",
    "machine",
    "dependencies",
    "benchmark_script_sha256",
)


def canonical_json(value: Any) -> bytes:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def workload(size: int) -> tuple[ContractBundle, dict[str, Any], dict[str, bytes]]:
    """Build a deterministic positive-rule fanout with fully declared evidence."""
    if not 1 <= size <= MAX_ACTIONS:
        raise ValueError(f"workload size must be between 1 and {MAX_ACTIONS}")
    names = [f"control{index:04d}" for index in range(size)]
    evidence = {
        f"reviews/{name}.json": canonical_json(
            {"control": name, "reviewed": True, "result": "review completed"}
        )
        for name in names
    }
    bundle = ContractBundle.model_validate(
        {
            "manifest": {
                "pack_format_version": 3,
                "framework_id": "benchmark",
                "version": "1.0.0",
                "requires_engine": ">=3.0.0,<4",
                "contracts": {
                    "flags": ["flags.yml"],
                    "actions": ["actions.yml"],
                    "rules": ["rules.yml"],
                    "articles": ["articles.yml"],
                },
                "runtime": {
                    "semantics": ["semantics.yml"],
                    "policies": ["policies.yml"],
                    "presentation": ["presentation.yml"],
                },
                "templates_dir": "templates",
                "evidence_templates_dir": "evidence_templates",
                "runtime_files": {
                    "context_defaults": "context.yml",
                    "backlog_config": "backlog.yml",
                },
            },
            "flags": {
                "registry": [{"id": "scope.included"}]
                + [{"id": f"review.{name}"} for name in names]
            },
            "actions": {
                "actions": [
                    {
                        "id": name,
                        "title": f"Review {name}",
                        "when": {"has": f"review.{name}"},
                        "articles": [f"article.{name}"],
                        "evidence": [f"reviews/{name}.json"],
                    }
                    for name in names
                ]
            },
            "rules": {
                "derivations": [
                    {
                        "id": f"derive.{name}",
                        "when": {"has": "scope.included"},
                        "set_flags": [f"review.{name}"],
                    }
                    for name in names
                ]
            },
            "articles": {"taxonomy": [{"id": f"article.{name}"} for name in names]},
            "runtime": {"semantics": {"framework_id": "benchmark"}},
        }
    )
    return bundle, {"flags": ["scope.included"]}, evidence


def workload_provenance(
    bundle: ContractBundle, answers: dict[str, Any], evidence: dict[str, bytes]
) -> dict[str, Any]:
    files = {name: digest(content) for name, content in sorted(evidence.items())}
    components = {
        "bundle_sha256": compute_bundle_hash(bundle),
        "assessment_input_sha256": digest(canonical_json(answers)),
        "evidence_inventory_sha256": digest(canonical_json(files)),
    }
    return {
        **components,
        "workload_sha256": digest(canonical_json(components)),
        "evidence_files": len(evidence),
        "evidence_bytes": sum(map(len, evidence.values())),
    }


def sample_summary(samples: list[float]) -> dict[str, Any]:
    return {
        "samples": samples,
        "min": min(samples),
        "median": statistics.median(samples),
        "max": max(samples),
    }


def measure(operation: Callable[[], ResultT], repeats: int) -> tuple[ResultT, dict[str, Any]]:
    """Time untraced repeats; measure traced peak allocation in a separate pass."""
    operation()  # One warmup, excluded from measurements.
    elapsed: list[float] = []
    cpu: list[float] = []
    for _ in range(repeats):
        wall_start, cpu_start = time.perf_counter_ns(), time.process_time_ns()
        operation()
        cpu.append((time.process_time_ns() - cpu_start) / 1_000_000_000)
        elapsed.append((time.perf_counter_ns() - wall_start) / 1_000_000_000)
    tracemalloc.start()
    try:
        result = operation()
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    return result, {
        "elapsed_seconds": sample_summary(elapsed),
        "cpu_seconds": sample_summary(cpu),
        "traced_peak_bytes": peak,
        "memory_passes": 1,
        "timed_repeats": repeats,
        "warmups": 1,
    }


def ready_snapshot(plan: dict[str, Any], root: Path) -> EvidenceSnapshot:
    snapshot = collect_evidence(plan, root)
    problems = check_ci(plan, snapshot)
    if problems:
        raise ValueError("synthetic evidence failed CI validation: " + "; ".join(problems))
    return snapshot


def benchmark_size(size: int, repeats: int, root: Path) -> dict[str, Any]:
    bundle, answers, evidence = workload(size)
    for relative, content in evidence.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    plan, assessment = measure(lambda: assess_from_bundle(bundle, answers), repeats)
    if len(plan["actions"]) != size or len(plan["flags"]) != size + 1:
        raise ValueError("synthetic assessment did not select the declared workload")
    snapshot, collection = measure(lambda: ready_snapshot(plan, root), repeats)
    if snapshot.files != evidence or set(snapshot.quality.values()) != {"ready"}:
        raise ValueError("synthetic evidence changed bytes or readiness")
    return {
        "action_count": size,
        "derivation_count": size,
        "article_count": size,
        "selected_action_count": len(plan["actions"]),
        "ready_evidence_count": len(snapshot.files),
        "semantic_plan_sha256": plan["trace"]["plan_hash"],
        "evidence_snapshot_sha256": digest(
            canonical_json(
                {
                    "files": {name: digest(content) for name, content in snapshot.files.items()},
                    "quality": snapshot.quality,
                }
            )
        ),
        "input": workload_provenance(bundle, answers, evidence),
        "assessment": assessment,
        "evidence_collection_and_ci": collection,
    }


def source_provenance() -> dict[str, str]:
    package_file = intrinsical_policy_engine.__file__
    if package_file is None:
        raise ValueError("engine package has no source location")
    root = Path(package_file).parent
    files = {
        path.relative_to(root).as_posix(): digest(path.read_bytes())
        for path in sorted(root.rglob("*.py"))
    }
    return {
        "engine_source_sha256": digest(canonical_json(files)),
        "benchmark_script_sha256": digest(Path(__file__).read_bytes()),
    }


def run_benchmarks(sizes: Sequence[int], repeats: int) -> dict[str, Any]:
    if not sizes or len(sizes) > 10 or len(set(sizes)) != len(sizes):
        raise ValueError("provide between one and ten distinct workload sizes")
    if any(not 1 <= size <= MAX_ACTIONS for size in sizes):
        raise ValueError(f"workload sizes must be between 1 and {MAX_ACTIONS}")
    if not 1 <= repeats <= MAX_REPEATS:
        raise ValueError(f"repeats must be between 1 and {MAX_REPEATS}")
    provenance = source_provenance()
    observations = []
    with tempfile.TemporaryDirectory(prefix="ipe-benchmark-") as temporary:
        for size in sizes:
            root = Path(temporary) / str(size)
            root.mkdir()
            observations.append(benchmark_size(size, repeats, root))
    if source_provenance() != provenance:
        raise ValueError(
            "engine or benchmark source changed during measurement; retry a stable tree"
        )
    return {
        "benchmark_format_version": 1,
        "measured_at": datetime.now(UTC).isoformat(),
        "environment": {
            "engine_version": CANONICAL_ENGINE_VERSION,
            "python_version": platform.python_version(),
            "python_implementation": platform.python_implementation(),
            "python_compiler": platform.python_compiler(),
            "platform": platform.platform(),
            "machine": platform.machine(),
            "dependencies": {name: version(name) for name in ("pydantic", "PyYAML", "jinja2")},
            **provenance,
        },
        "method": {
            "workload": "positive derivation fanout; one action, article and JSON file per rule",
            "setup_measured": False,
            "assessment_includes": (
                "programmatic validation, derivation, selection and plan hashing"
            ),
            "evidence_includes": "filesystem reads, byte quality classification and check_ci",
            "memory": "separate tracemalloc pass; Python allocation peak, not process RSS",
            "timing": "untraced perf_counter and process_time samples after one warmup",
            "interpretation": "observed local baseline only; no SLO or capacity guarantee",
            "exclusions": [
                "YAML pack compilation",
                "template rendering",
                "snapshot export and sealing",
                "provider or network IO",
                "worst-case derivation chains",
            ],
        },
        "workloads": observations,
    }


def _workload_index(report: Mapping[str, Any], label: str) -> dict[int, dict[str, Any]]:
    if (
        type(report.get("benchmark_format_version")) is not int
        or report.get("benchmark_format_version") != 1
    ):
        raise ValueError(f"{label}: unsupported benchmark_format_version")
    workloads = report.get("workloads")
    if not isinstance(workloads, list) or not workloads:
        raise ValueError(f"{label}: workloads must be a nonempty list")
    result = {}
    for workload_data in workloads:
        if not isinstance(workload_data, dict):
            raise ValueError(f"{label}: workload must be an object")
        size = workload_data.get("action_count")
        if type(size) is not int or not 1 <= size <= MAX_ACTIONS or size in result:
            raise ValueError(f"{label}: action_count must be unique and within benchmark bounds")
        provenance = workload_data.get("input")
        fingerprint = provenance.get("workload_sha256") if isinstance(provenance, dict) else None
        if (
            not isinstance(fingerprint, str)
            or len(fingerprint) != 64
            or any(character not in "0123456789abcdef" for character in fingerprint)
        ):
            raise ValueError(f"{label} workload {size}: invalid input workload_sha256")
        result[size] = workload_data
    return result


def _phase_median(workload_data: Mapping[str, Any], phase: str, label: str) -> float:
    metrics = workload_data.get(phase)
    if not isinstance(metrics, dict) or not isinstance(metrics.get("elapsed_seconds"), dict):
        raise ValueError(f"{label}: {phase} elapsed_seconds must be an object")
    median = metrics["elapsed_seconds"].get("median")
    if type(median) not in (int, float):
        raise ValueError(f"{label}: {phase} median must be a positive finite number")
    try:
        numeric_median = float(median)
    except OverflowError as error:
        raise ValueError(f"{label}: {phase} median exceeds numeric bounds") from error
    if not math.isfinite(numeric_median) or numeric_median <= 0:
        raise ValueError(f"{label}: {phase} median must be a positive finite number")
    for key in ("timed_repeats", "warmups", "memory_passes"):
        if type(metrics.get(key)) is not int or metrics[key] < 1:
            raise ValueError(f"{label}: {phase} {key} must be a positive integer")
    return numeric_median


def _require_comparable_environment(
    current: Mapping[str, Any], baseline: Mapping[str, Any]
) -> None:
    current_environment, baseline_environment = (
        current.get("environment"),
        baseline.get("environment"),
    )
    if not isinstance(current_environment, dict) or not isinstance(baseline_environment, dict):
        raise ValueError("reports must include environment provenance objects")
    for key in COMPARABLE_ENVIRONMENT:
        if key not in current_environment or key not in baseline_environment:
            raise ValueError(f"reports lack comparable environment.{key}")
        if current_environment[key] != baseline_environment[key]:
            raise ValueError(f"reports are not comparable: environment.{key} differs")
    if not isinstance(current.get("method"), dict) or not current["method"]:
        raise ValueError("current report lacks a measurement method")
    if current["method"] != baseline.get("method"):
        raise ValueError("reports are not comparable: measurement method differs")


def compare_reports(
    current: Mapping[str, Any],
    baseline: Mapping[str, Any],
    *,
    baseline_sha256: str,
    max_slowdown: float,
) -> dict[str, Any]:
    """Gate elapsed medians under an explicit local tolerance; never infer an SLO."""
    if not math.isfinite(max_slowdown) or max_slowdown < 0:
        raise ValueError("max_slowdown must be a finite nonnegative fraction")
    current_workloads = _workload_index(current, "current")
    baseline_workloads = _workload_index(baseline, "baseline")
    _require_comparable_environment(current, baseline)
    if current_workloads.keys() != baseline_workloads.keys():
        raise ValueError("reports are not comparable: workload sizes differ")
    comparisons = []
    for size, workload_data in sorted(current_workloads.items()):
        prior = baseline_workloads[size]
        fingerprint = workload_data["input"]["workload_sha256"]
        if fingerprint != prior["input"]["workload_sha256"]:
            raise ValueError(f"reports are not comparable: workload {size} input hashes differ")
        phases = {}
        for phase in TIMED_PHASES:
            current_median = _phase_median(workload_data, phase, f"current workload {size}")
            baseline_median = _phase_median(prior, phase, f"baseline workload {size}")
            for key in ("timed_repeats", "warmups", "memory_passes"):
                if workload_data[phase][key] != prior[phase][key]:
                    raise ValueError(f"reports are not comparable: workload {size} {phase} {key}")
            ratio = current_median / baseline_median
            if not math.isfinite(ratio):
                raise ValueError(f"workload {size}: elapsed median ratio exceeds numeric bounds")
            phases[phase] = {
                "baseline_median_seconds": baseline_median,
                "current_median_seconds": current_median,
                "ratio": ratio,
                "passed": ratio <= 1 + max_slowdown,
            }
        comparisons.append({"action_count": size, "workload_sha256": fingerprint, "phases": phases})
    return {
        "baseline_sha256": baseline_sha256,
        "max_slowdown": max_slowdown,
        "max_ratio": 1 + max_slowdown,
        "scope": "median elapsed seconds only; allocation peaks and CPU time are not gated",
        "environment_keys": COMPARABLE_ENVIRONMENT,
        "engine_source_hashes_may_differ": True,
        "passed": all(phase["passed"] for item in comparisons for phase in item["phases"].values()),
        "workloads": comparisons,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sizes", type=int, nargs="+", default=[10, 100, 1000])
    parser.add_argument("--repeats", type=int, default=3)
    parser.add_argument(
        "--output", type=Path, required=True, help="New JSON report; never overwritten"
    )
    parser.add_argument("--baseline", type=Path, help="Existing comparable benchmark JSON report")
    parser.add_argument(
        "--max-slowdown",
        type=float,
        help="Required with --baseline; allowed median slowdown fraction (0.25 permits 1.25x)",
    )
    arguments = parser.parse_args(argv)
    if arguments.output.exists() or arguments.output.is_symlink():
        parser.error("--output already exists; choose a new report path")
    if (arguments.baseline is None) != (arguments.max_slowdown is None):
        parser.error("--baseline and --max-slowdown must be provided together")
    if arguments.max_slowdown is not None and (
        not math.isfinite(arguments.max_slowdown) or arguments.max_slowdown < 0
    ):
        parser.error("--max-slowdown must be a finite nonnegative fraction")
    try:
        baseline_bytes = arguments.baseline.read_bytes() if arguments.baseline is not None else None
        try:
            baseline = json.loads(baseline_bytes) if baseline_bytes is not None else None
        except RecursionError as error:
            raise ValueError("baseline JSON nesting exceeds supported decoder limits") from error
        if baseline_bytes is not None and not isinstance(baseline, dict):
            raise ValueError("baseline report must be a JSON object")
        if isinstance(baseline, dict):
            _workload_index(baseline, "baseline")
        report = run_benchmarks(arguments.sizes, arguments.repeats)
        if isinstance(baseline, dict) and baseline_bytes is not None:
            report["comparison"] = compare_reports(
                report,
                baseline,
                baseline_sha256=digest(baseline_bytes),
                max_slowdown=arguments.max_slowdown,
            )
    except (OSError, ValueError) as error:
        parser.error(str(error))
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    with arguments.output.open("x", encoding="utf-8") as output:
        json.dump(report, output, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        output.write("\n")
    print(f"Wrote {len(report['workloads'])} measured workloads to {arguments.output}")
    if not report.get("comparison", {}).get("passed", True):
        print(
            "Timing regression exceeds the explicit baseline tolerance; see receipt.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
