"""Operational log correlation, valid records and filesystem boundaries."""

from __future__ import annotations

import json
import os
import stat
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import pytest

from intrinsical_policy_engine.adapters.logging import FsLogger


def strict_records(path):
    def invalid_constant(value):
        raise ValueError(f"Invalid JSON constant: {value}")

    return [
        json.loads(line, parse_constant=invalid_constant) for line in path.read_text().splitlines()
    ]


def test_records_correlate_one_invocation_and_identify_command(tmp_path):
    path = tmp_path / "run.jsonl"
    logger = FsLogger(path, command="export")
    logger.info("command.start")
    logger.warning("export.warning", {"files": 3})
    logger.error("command.failure", {"code": "EXPORT-001", "duration_ms": 12.5})
    records = strict_records(path)
    assert len({record["run_id"] for record in records}) == 1
    assert str(UUID(records[0]["run_id"])) == logger.run_id
    assert all(record["command"] == "export" for record in records)
    assert [record["level"] for record in records] == ["INFO", "WARNING", "ERROR"]
    assert records[-1]["data"] == {"code": "EXPORT-001", "duration_ms": 12.5}
    assert datetime.fromisoformat(records[0]["timestamp"]).tzinfo == UTC


def test_reusing_path_appends_and_has_a_new_invocation_id(tmp_path):
    path = tmp_path / "history.jsonl"
    first = FsLogger(path)
    first.info("first")
    preserved = path.read_bytes()
    second = FsLogger(path)
    second.info("second", {"count": 2})
    assert path.read_bytes().startswith(preserved)
    records = strict_records(path)
    assert [record["event"] for record in records] == ["first", "second"]
    assert records[0]["run_id"] != records[1]["run_id"]
    assert "command" not in records[0]


def test_append_separates_an_existing_complete_record_without_final_newline(tmp_path):
    path = tmp_path / "no-final-newline.jsonl"
    previous = b'{"event":"prior","data":{"note":"retained"}}'
    path.write_bytes(previous)
    FsLogger(path).info("next")
    assert path.read_bytes().startswith(previous + b"\n")
    assert [record["event"] for record in strict_records(path)] == ["prior", "next"]


@pytest.mark.parametrize(
    "tail",
    [
        b'{"private":"canary',
        b"not-json",
        b"\xff",
        b'{"measure":NaN}',
        b'{"measure":1e1000}',
        b'{"event":"first","event":"second"}',
    ],
)
def test_append_rejects_a_truncated_tail_without_changing_prior_bytes(tmp_path, tail):
    path = tmp_path / "truncated.jsonl"
    previous = b'{"event":"prior"}\n' + tail
    path.write_bytes(previous)
    with pytest.raises(OSError, match="operational log") as failure:
        FsLogger(path).info("next")
    assert path.read_bytes() == previous
    assert "canary" not in str(failure.value)


def test_append_separates_a_large_prior_record_under_the_shared_lock(tmp_path):
    path = tmp_path / "large-tail.jsonl"
    previous = json.dumps({"event": "prior", "data": "ñ" * 80_000}).encode()
    path.write_bytes(previous)
    loggers = [FsLogger(path) for _ in range(4)]
    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(lambda logger: logger.info("next"), loggers))
    assert len(strict_records(path)) == 5
    assert path.read_bytes().startswith(previous + b"\n")


@pytest.mark.parametrize(
    "data",
    [
        {"value": float("nan")},
        {"value": float("inf")},
        {"value": float("-inf")},
        {"nested": [{"value": float("inf")}]},
        {"value": Path("unstructured-object")},
    ],
)
def test_invalid_log_data_fails_before_file_creation(tmp_path, data):
    path = tmp_path / "invalid.jsonl"
    logger = FsLogger(path)
    with pytest.raises(ValueError, match="serialize log event"):
        logger.info("invalid.data", data)
    assert not path.exists()


def test_serialization_failure_preserves_prior_records(tmp_path):
    path = tmp_path / "existing.jsonl"
    logger = FsLogger(path)
    logger.info("valid", {"count": 1})
    previous = path.read_bytes()
    with pytest.raises(ValueError, match="serialize log event"):
        logger.info("invalid", {"count": float("inf")})
    assert path.read_bytes() == previous


@pytest.mark.parametrize("dangling", [False, True])
def test_log_file_symlinks_never_append_to_referent(tmp_path, dangling):
    target = tmp_path / "target"
    if not dangling:
        target.write_text("preserved\n")
    path = tmp_path / "log-link.jsonl"
    path.symlink_to(target)
    with pytest.raises(OSError, match="operational log"):
        FsLogger(path).info("must.fail")
    assert path.is_symlink()
    if dangling:
        assert not target.exists()
    else:
        assert target.read_text() == "preserved\n"


def test_hardlinked_log_file_is_not_modified(tmp_path):
    target = tmp_path / "target"
    target.write_text("preserved\n")
    path = tmp_path / "log-hardlink.jsonl"
    path.hardlink_to(target)
    with pytest.raises(OSError, match="operational log"):
        FsLogger(path).info("must.fail")
    assert target.read_text() == "preserved\n"


def test_log_write_failure_identifies_operational_log(tmp_path):
    path = tmp_path / "directory.jsonl"
    path.mkdir()
    with pytest.raises(OSError, match="operational log") as failure:
        FsLogger(path).info("must.fail")
    assert str(path) in str(failure.value)


def test_fifo_log_is_rejected_without_blocking(tmp_path):
    path = tmp_path / "fifo.jsonl"
    os.mkfifo(path)
    with pytest.raises(OSError, match="operational log"):
        FsLogger(path).info("must.fail")
    assert stat.S_ISFIFO(path.stat().st_mode)


def test_created_log_file_permissions_are_private(tmp_path):
    path = tmp_path / "private.jsonl"
    FsLogger(path).info("created")
    assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0


def test_concurrent_invocations_preserve_complete_long_records(tmp_path):
    path = tmp_path / "shared.jsonl"
    loggers = [FsLogger(path) for _ in range(4)]
    payload = "ña" * 20_000

    def write(logger):
        for position in range(5):
            logger.info("parallel", {"position": position, "payload": payload})

    with ThreadPoolExecutor(max_workers=4) as executor:
        list(executor.map(write, loggers))
    records = strict_records(path)
    assert len(records) == 20
    assert {record["run_id"] for record in records} == {logger.run_id for logger in loggers}
    assert all(record["data"]["payload"] == payload for record in records)


def test_file_logger_does_not_write_to_console(tmp_path, capsys):
    logger = FsLogger(tmp_path / "silent.jsonl")
    logger.info("info")
    logger.warning("warning")
    logger.error("error")
    captured = capsys.readouterr()
    assert captured.out == captured.err == ""
