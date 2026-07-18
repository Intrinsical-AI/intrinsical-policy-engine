# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.

from __future__ import annotations

import json
from pathlib import Path

from src.adapters.quality.engine import QualityEngine
from src.app.use_cases.seal import collect_seal_input


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_evidence_quality_accepts_required_empty_maps(tmp_path: Path) -> None:
    report = tmp_path / "evidence_quality.json"
    _write_json(
        report,
        {
            "quality_by_file": {},
            "missing_reasons_by_article": {},
        },
    )

    assert QualityEngine().diagnose_file(report) == (True, "ok")


def test_evidence_quality_rejects_missing_required_map(tmp_path: Path) -> None:
    report = tmp_path / "evidence_quality.json"
    _write_json(report, {"quality_by_file": {}})

    assert QualityEngine().diagnose_file(report) == (False, "json_insufficient_keys")


def test_evidence_quality_rejects_wrong_required_map_type(tmp_path: Path) -> None:
    report = tmp_path / "evidence_quality.json"
    _write_json(
        report,
        {
            "quality_by_file": [],
            "missing_reasons_by_article": {},
        },
    )

    assert QualityEngine().diagnose_file(report) == (False, "json_insufficient_keys")


def test_evidence_quality_rejects_malformed_json(tmp_path: Path) -> None:
    report = tmp_path / "evidence_quality.json"
    report.write_text("{", encoding="utf-8")

    assert QualityEngine().diagnose_file(report) == (False, "json_parse_error")


def test_generic_json_still_uses_non_empty_key_threshold(tmp_path: Path) -> None:
    report = tmp_path / "other.json"
    _write_json(report, {"one": 1, "two": 2})

    assert QualityEngine().diagnose_file(report) == (False, "json_insufficient_keys")


def test_seal_collection_does_not_report_valid_evidence_quality(tmp_path: Path) -> None:
    report = tmp_path / "_metadata" / "evidence_quality.json"
    _write_json(
        report,
        {
            "quality_by_file": {},
            "missing_reasons_by_article": {},
        },
    )

    seal_input = collect_seal_input(tmp_path, quality_engine=QualityEngine())

    assert seal_input.quality_issues == ()
