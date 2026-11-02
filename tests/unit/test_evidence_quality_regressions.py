"""Reject empty or malformed evidence before CI and strict sealing succeed."""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

import pytest
import yaml

from intrinsical_policy_engine.adapters.quality.engine import QualityEngine
from intrinsical_policy_engine.app.cli.main import main
from intrinsical_policy_engine.common.package_integrity import write_checksums
from intrinsical_policy_engine.common.text.front_matter import strip_front_matter
from intrinsical_policy_engine.domain.snapshot_contract import CHECKSUMS_NAME

READY_BODY = "## Completed review\n" + (
    "Completed assessed controls reviewed and results documented. " * 20
)
METADATA_ONLY = (
    '---\ntitle: completed-review\n## Completed review\ndescription: "'
    + "Completed assessed controls reviewed and results documented. " * 20
    + '"\n---'
)


@pytest.mark.parametrize(
    "row", ['"control"junk,complete', '"control,complete', '"control" ,complete']
)
def test_malformed_csv_has_a_controlled_error(row: str) -> None:
    evidence = "review,result\n" + "control,complete\n" * 5 + row
    with pytest.raises(ValueError, match="Invalid CSV evidence"):
        QualityEngine().classify_bytes(evidence.encode(), ".csv")


@pytest.mark.parametrize("header", [",", " , \t", "review,", " ,result"])
def test_csv_requires_nonblank_column_names(header: str) -> None:
    evidence = header + "\n" + "control,complete\n" * 5
    assert QualityEngine().classify_bytes(evidence.encode(), ".csv") == "placeholder"


@pytest.mark.parametrize("row", [",", " , \t", ""])
def test_empty_csv_rows_cannot_supply_evidence(row: str) -> None:
    evidence = "review,result\n" + (row + "\n") * 5
    assert QualityEngine().classify_bytes(evidence.encode(), ".csv") == "placeholder"


def test_blank_rows_do_not_inflate_csv_readiness() -> None:
    evidence = "review,result\n" + "control,complete\n" * 2 + ",\n" * 5
    assert QualityEngine().classify_bytes(evidence.encode(), ".csv") == "draft"


@pytest.mark.parametrize(
    "data_rows,quality",
    [(0, "placeholder"), (1, "placeholder"), (2, "draft"), (4, "draft"), (5, "ready")],
)
def test_csv_thresholds_count_data_rows(data_rows: int, quality: str) -> None:
    evidence = "review,result\n" + "control,complete\n" * data_rows
    assert QualityEngine().classify_bytes(evidence.encode(), ".csv") == quality


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("prefix", ["", "\ufeff", " \t\n  ", "\ufeff \t\n  "])
@pytest.mark.parametrize("trailing_newline", [False, True])
def test_front_matter_without_body_never_grants_readiness(
    newline: str, prefix: str, trailing_newline: bool
) -> None:
    content = prefix + METADATA_ONLY + ("\n" if trailing_newline else "")
    evidence = content.replace("\n", newline)
    assert QualityEngine().classify_bytes(evidence.encode(), ".md") == "placeholder"


def test_unclosed_front_matter_does_not_become_markdown_body() -> None:
    evidence = METADATA_ONLY.removesuffix("\n---")
    assert QualityEngine().classify_bytes(evidence.encode(), ".md") == "placeholder"


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_front_matter_delimiters_allow_whitespace_and_preserve_the_body(newline: str) -> None:
    body = READY_BODY.replace("\n", newline)
    evidence = "\ufeff \t" + newline + "  --- \t" + newline + "status: draft" + newline
    evidence += "  --- \t" + newline + body
    assert strip_front_matter(evidence) == body
    assert QualityEngine().classify_bytes(evidence.encode(), ".md") == "ready"


@pytest.mark.parametrize("prefix", ["", "\ufeff", " \t\n", "\ufeff \t\n"])
def test_markdown_without_front_matter_preserves_content(prefix: str) -> None:
    content = prefix + READY_BODY + "\n---\nReview complete."
    assert strip_front_matter(content) == content.removeprefix("\ufeff")


def invoke(monkeypatch: pytest.MonkeyPatch, *arguments: str | Path) -> int:
    monkeypatch.setattr(sys, "argv", ["ipe", *map(str, arguments)])
    return main()


@pytest.mark.parametrize(
    "suffix,invalid",
    [
        (".csv", "review,result\n" + '"control"junk,complete\n' * 5),
        (".csv", ",\n" + "control,complete\n" * 5),
        (".csv", "review,result\n" + " , \t\n" * 5),
        (".md", METADATA_ONLY),
    ],
    ids=["malformed-csv", "empty-csv-header", "empty-csv-rows", "metadata-only-markdown"],
)
def test_invalid_evidence_fails_export_ci_and_strict_sealing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, suffix: str, invalid: str
) -> None:
    pack = tmp_path / "pack"
    shutil.copytree("frameworks/starter", pack)
    relative = "starter/review" + suffix
    actions_path = pack / "law/core/actions.yml"
    actions = yaml.safe_load(actions_path.read_text())
    actions["actions"][0]["evidence"] = [relative]
    actions_path.write_text(yaml.safe_dump(actions))
    (pack / "law/policy/evidence_map.yml").write_text(
        yaml.safe_dump({"TOPIC-STARTER-CONTROLS": [{"path": relative}]})
    )
    evidence_root = tmp_path / "evidence"
    evidence_file = evidence_root / relative
    evidence_file.parent.mkdir(parents=True)
    evidence_file.write_text(invalid)
    common = (
        "--contracts",
        pack,
        "--answers",
        "demos/starter/basic/answers.json",
        "--evidence-dir",
        evidence_root,
    )
    rejected = tmp_path / "rejected"
    assert invoke(monkeypatch, "export", *common, "--out", rejected, "--check-ci", "--strict") == 1
    assert not rejected.exists()

    # A complete baseline from another producer must still undergo evidence checks.
    valid = READY_BODY if suffix == ".md" else "review,result\n" + "control,complete\n" * 5
    evidence_file.write_text(valid)
    snapshot = tmp_path / "snapshot"
    assert invoke(monkeypatch, "export", *common, "--out", snapshot, "--check-ci", "--strict") == 0
    (snapshot / "evidence" / relative).write_text(invalid)
    (snapshot / CHECKSUMS_NAME).unlink()
    write_checksums(snapshot)
    archive = tmp_path / "snapshot.zip"
    assert (
        invoke(
            monkeypatch,
            "seal",
            "--export-dir",
            snapshot,
            "--output",
            archive,
            "--no-sign",
            "--strict",
        )
        == 1
    )
    assert not archive.exists()
