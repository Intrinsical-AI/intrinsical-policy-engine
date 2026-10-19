# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Benchmark receipts bind reproducible inputs to actual measured operations."""

from __future__ import annotations

import copy
import hashlib
import json

import pytest

from scripts import benchmark_engine
from scripts.benchmark_engine import (
    compare_reports,
    main,
    run_benchmarks,
    workload,
    workload_provenance,
)


def test_workload_provenance_is_reproducible_and_changes_with_inputs():
    bundle, answers, evidence = workload(3)
    expected = workload_provenance(bundle, answers, evidence)
    assert workload_provenance(*workload(3)) == expected
    assert workload_provenance(*workload(4))["workload_sha256"] != expected["workload_sha256"]
    assert workload_provenance(bundle, {"flags": []}, evidence) != expected
    assert workload_provenance(bundle, answers, {**evidence, "other.json": b"{}"}) != expected


def test_benchmark_measures_real_assessment_and_ready_evidence():
    report = run_benchmarks([3], 1)
    assert report["benchmark_format_version"] == 1
    assert report["environment"]["python_version"]
    assert len(report["environment"]["engine_source_sha256"]) == 64
    assert len(report["environment"]["benchmark_script_sha256"]) == 64
    measured = report["workloads"][0]
    assert measured["selected_action_count"] == measured["ready_evidence_count"] == 3
    assert len(measured["semantic_plan_sha256"]) == len(measured["evidence_snapshot_sha256"]) == 64
    assert measured["input"] == workload_provenance(*workload(3))
    for phase in ("assessment", "evidence_collection_and_ci"):
        metrics = measured[phase]
        assert metrics["traced_peak_bytes"] > 0
        assert metrics["timed_repeats"] == 1
        assert len(metrics["elapsed_seconds"]["samples"]) == 1
        assert metrics["elapsed_seconds"]["min"] >= 0
    assert "not process RSS" in report["method"]["memory"]
    assert "snapshot export and sealing" in report["method"]["exclusions"]


@pytest.mark.parametrize(
    "sizes,repeats", [([], 1), ([0], 1), ([1001], 1), ([1, 1], 1), ([1], 0), ([1], 21)]
)
def test_benchmark_rejects_unbounded_or_invalid_work(sizes, repeats):
    with pytest.raises(ValueError):
        run_benchmarks(sizes, repeats)


def test_benchmark_writes_a_standalone_receipt_and_refuses_overwrite(tmp_path):
    output = tmp_path / "receipt.json"
    assert main(["--sizes", "2", "--repeats", "1", "--output", str(output)]) == 0
    receipt = output.read_bytes()
    assert json.loads(receipt)["workloads"][0]["action_count"] == 2
    with pytest.raises(SystemExit) as failure:
        main(["--output", str(output)])
    assert failure.value.code == 2
    assert output.read_bytes() == receipt


def test_benchmark_refuses_a_receipt_when_source_changes_during_measurement(monkeypatch):
    sources = iter([{"engine_source_sha256": "before"}, {"engine_source_sha256": "after"}])
    monkeypatch.setattr(benchmark_engine, "source_provenance", lambda: next(sources))
    with pytest.raises(ValueError, match="source changed during measurement"):
        run_benchmarks([1], 1)


@pytest.fixture(scope="module")
def observed_report():
    return run_benchmarks([2], 1)


def controlled_report(observed, assessment: float = 1.0, evidence: float = 4.0):
    """Keep real provenance and workload, replacing only timing samples."""
    report = copy.deepcopy(observed)
    for phase, median in (("assessment", assessment), ("evidence_collection_and_ci", evidence)):
        report["workloads"][0][phase]["elapsed_seconds"] = {
            "min": median,
            "median": median,
            "max": median,
            "samples": [median],
        }
    return report


def test_explicit_timing_gate_accepts_the_boundary_and_allows_engine_changes(observed_report):
    baseline = controlled_report(observed_report)
    current = controlled_report(observed_report, assessment=1.25, evidence=5.0)
    current["environment"]["engine_source_sha256"] = "f" * 64
    current["environment"]["engine_version"] = "3.1.0"
    current["workloads"][0]["assessment"]["traced_peak_bytes"] *= 1_000
    comparison = compare_reports(current, baseline, baseline_sha256="a" * 64, max_slowdown=0.25)
    assert comparison["passed"] is True
    assert comparison["max_ratio"] == 1.25
    assert comparison["baseline_sha256"] == "a" * 64
    assert comparison["engine_source_hashes_may_differ"] is True
    assert "allocation peaks" in comparison["scope"]
    for phase in comparison["workloads"][0]["phases"].values():
        assert phase["ratio"] == 1.25
        assert phase["passed"] is True


@pytest.mark.parametrize("phase", ["assessment", "evidence_collection_and_ci"])
def test_explicit_timing_gate_fails_if_either_phase_exceeds_tolerance(observed_report, phase):
    baseline = controlled_report(observed_report, assessment=1.0, evidence=1.0)
    current = controlled_report(observed_report, assessment=1.0, evidence=1.0)
    current["workloads"][0][phase]["elapsed_seconds"]["median"] = 1.5
    comparison = compare_reports(current, baseline, baseline_sha256="a" * 64, max_slowdown=0.25)
    assert comparison["passed"] is False
    assert comparison["workloads"][0]["phases"][phase] == {
        "baseline_median_seconds": 1.0,
        "current_median_seconds": 1.5,
        "ratio": 1.5,
        "passed": False,
    }


@pytest.mark.parametrize(
    "field",
    [
        "python_version",
        "python_implementation",
        "python_compiler",
        "platform",
        "machine",
        "dependencies",
        "benchmark_script_sha256",
    ],
)
def test_gate_rejects_incompatible_execution_environments(observed_report, field):
    baseline = controlled_report(observed_report)
    baseline["environment"][field] = "incompatible"
    with pytest.raises(ValueError, match="environment." + field):
        compare_reports(
            controlled_report(observed_report),
            baseline,
            baseline_sha256="a" * 64,
            max_slowdown=0.25,
        )


@pytest.mark.parametrize(
    "path,value,match",
    [
        (("benchmark_format_version",), 2, "unsupported benchmark_format_version"),
        (("benchmark_format_version",), True, "unsupported benchmark_format_version"),
        (("workloads",), [], "nonempty list"),
        (("workloads", 0, "action_count"), 3, "workload sizes differ"),
        (("workloads", 0, "input"), None, "invalid input workload_sha256"),
        (("workloads", 0, "input", "workload_sha256"), "f" * 64, "input hashes differ"),
        (("method", "timing"), "different method", "measurement method differs"),
        (("workloads", 0, "assessment", "warmups"), 2, "warmups"),
        (("workloads", 0, "assessment", "timed_repeats"), 2, "timed_repeats"),
        (("workloads", 0, "assessment", "elapsed_seconds", "median"), 0, "positive finite"),
        (("workloads", 0, "assessment", "elapsed_seconds", "median"), -1, "positive finite"),
        (
            ("workloads", 0, "assessment", "elapsed_seconds", "median"),
            float("nan"),
            "positive finite",
        ),
        (
            ("workloads", 0, "assessment", "elapsed_seconds", "median"),
            float("inf"),
            "positive finite",
        ),
        (("workloads", 0, "assessment", "elapsed_seconds", "median"), "1", "positive finite"),
        (("workloads", 0, "assessment", "elapsed_seconds", "median"), True, "positive finite"),
        (("workloads", 0, "assessment", "elapsed_seconds", "median"), 10**1000, "numeric bounds"),
    ],
)
def test_gate_rejects_malformed_or_incomparable_baselines(observed_report, path, value, match):
    current = controlled_report(observed_report)
    baseline = copy.deepcopy(current)
    target = baseline
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value
    with pytest.raises(ValueError, match=match):
        compare_reports(current, baseline, baseline_sha256="a" * 64, max_slowdown=0.25)


@pytest.mark.parametrize("fraction", [-0.1, float("nan"), float("inf")])
def test_gate_requires_a_finite_nonnegative_tolerance(observed_report, fraction):
    with pytest.raises(ValueError, match="finite nonnegative fraction"):
        compare_reports(
            observed_report, observed_report, baseline_sha256="a" * 64, max_slowdown=fraction
        )


@pytest.mark.parametrize("assessment,exit_code,passed", [(1.25, 0, True), (1.5, 1, False)])
def test_cli_gate_writes_a_digest_bound_receipt_even_on_regression(
    tmp_path, monkeypatch, observed_report, assessment, exit_code, passed
):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps(controlled_report(observed_report)))
    output = tmp_path / "comparison.json"
    current = controlled_report(observed_report, assessment=assessment)
    monkeypatch.setattr(benchmark_engine, "run_benchmarks", lambda sizes, repeats: current)
    result = main(["--baseline", str(baseline), "--max-slowdown", "0.25", "--output", str(output)])
    assert result == exit_code
    receipt = json.loads(output.read_bytes())
    assert receipt["comparison"]["passed"] is passed
    assert (
        receipt["comparison"]["baseline_sha256"]
        == hashlib.sha256(baseline.read_bytes()).hexdigest()
    )
    assert receipt["comparison"]["max_slowdown"] == 0.25


@pytest.mark.parametrize("payload", ["{broken", "null", "[]", "{}"])
def test_cli_rejects_invalid_baselines_without_writing_a_receipt(tmp_path, payload):
    baseline = tmp_path / "baseline.json"
    baseline.write_text(payload)
    output = tmp_path / "comparison.json"
    with pytest.raises(SystemExit) as failure:
        main(["--baseline", str(baseline), "--max-slowdown", "0.25", "--output", str(output)])
    assert failure.value.code == 2
    assert not output.exists()


def test_cli_rejects_incompatible_baselines_without_writing_a_receipt(
    tmp_path, monkeypatch, observed_report
):
    baseline = tmp_path / "baseline.json"
    prior = controlled_report(observed_report)
    prior["environment"]["python_version"] = "0.0.0"
    baseline.write_text(json.dumps(prior))
    output = tmp_path / "comparison.json"
    monkeypatch.setattr(
        benchmark_engine,
        "run_benchmarks",
        lambda sizes, repeats: controlled_report(observed_report),
    )
    with pytest.raises(SystemExit) as failure:
        main(["--baseline", str(baseline), "--max-slowdown", "0.25", "--output", str(output)])
    assert failure.value.code == 2
    assert not output.exists()


@pytest.mark.parametrize("options", [["--baseline", "missing.json"], ["--max-slowdown", "0.25"]])
def test_cli_requires_an_explicit_limit_together_with_a_baseline(tmp_path, options):
    output = tmp_path / "comparison.json"
    with pytest.raises(SystemExit) as failure:
        main([*options, "--output", str(output)])
    assert failure.value.code == 2
    assert not output.exists()


def test_cli_rejects_a_deeply_nested_baseline_without_traceback_or_measurement(
    tmp_path, monkeypatch, capsys
):
    baseline = tmp_path / "baseline.json"
    baseline.write_text("[" * 10_000 + "]" * 10_000)
    output = tmp_path / "comparison.json"

    def unexpected_measurement(*args):
        raise AssertionError("invalid baseline must be rejected before any measurements")

    monkeypatch.setattr(benchmark_engine, "run_benchmarks", unexpected_measurement)
    with pytest.raises(SystemExit) as failure:
        main(["--baseline", str(baseline), "--max-slowdown", "0", "--output", str(output)])
    assert failure.value.code == 2
    assert "baseline" in capsys.readouterr().err.lower()
    assert not output.exists()
