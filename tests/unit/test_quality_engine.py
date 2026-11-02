# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Readiness follows content, including when metadata claims it is ready."""

from pathlib import Path

import pytest

from intrinsical_policy_engine.adapters.quality.engine import QualityEngine


@pytest.mark.parametrize(
    "data,suffix",
    [
        (b"---\nstatus: ready\n---\n## Review\nStub.", ".md"),
        (b'{"one": 1, "two": 2}', ".json"),
        (b'{"one": 1, "one": 2, "two": 3, "three": 4}', ".json"),
        (b"one: 1\none: 2\ntwo: 3\nthree: 4\n", ".yml"),
        (b"\xff", ".md"),
        (b"%PDF corrupt bytes", ".pdf"),
    ],
)
def test_invalid_or_incomplete_evidence_cannot_be_ready(data, suffix):
    assert QualityEngine().classify_bytes(data, suffix) != "ready"


def test_markdown_readiness_requires_real_body_and_structure(tmp_path: Path):
    content = "## Completed control review\n" + (
        "Reviewed control results and documented evidence findings. " * 15
    )
    file = tmp_path / "review.md"
    file.write_text(content)
    engine = QualityEngine()
    assert engine.classify_file(file) == "ready"
    assert engine.diagnose_file(file) == (True, "ok")
    file.write_text(content.replace("## Completed control review", "Completed control review"))
    assert engine.classify_file(file) == "draft"
    file.write_text(content + "\nTODO confirm results")
    assert engine.classify_file(file) == "placeholder"


def test_operational_read_errors_are_not_masked_as_quality(tmp_path: Path):
    with pytest.raises(FileNotFoundError):
        QualityEngine().classify_file(tmp_path / "missing.md")
