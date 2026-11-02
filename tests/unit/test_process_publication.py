# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Actual process death and contention never publish partial or mixed outputs."""

from __future__ import annotations

import json
import os
import selectors
import shutil
import signal
import subprocess
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest

from intrinsical_policy_engine.common.output_safety import fresh_output, output_lock_path

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="accepted publication platform")
TIMEOUT = 10
WORKER = r"""
import json
import sys
import time
from pathlib import Path
from intrinsical_policy_engine.common.output_safety import fresh_output

destination, mode, label = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
if mode == 'interrupt':
    with fresh_output(destination) as staging:
        (staging / 'partial.bin').write_bytes(b'unfinished caller data')
        print(json.dumps({'staging': str(staging)}), flush=True)
        sys.stdin.readline()
else:
    print('READY', flush=True)
    sys.stdin.readline()
    deadline = time.monotonic() + 5
    while True:
        try:
            with fresh_output(destination) as staging:
                (staging / 'winner.txt').write_text(label)
                (staging / 'payload.bin').write_bytes(label.encode() * 1024)
                time.sleep(0.08)
            print(json.dumps({'status': 'published', 'label': label}), flush=True)
            break
        except BlockingIOError:
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
        except FileExistsError:
            print(json.dumps({'status': 'FileExistsError', 'label': label}), flush=True)
            break
"""


@contextmanager
def worker(destination: Path, mode: str, label: str) -> Iterator[subprocess.Popen[str]]:
    environment = {key: value for key, value in os.environ.items() if not key.startswith("PYTHON")}
    with subprocess.Popen(
        [sys.executable, "-I", "-u", "-c", WORKER, str(destination), mode, label],
        cwd=destination.parent,
        env=environment,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    ) as process:
        try:
            yield process
        finally:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=TIMEOUT)


def ready_line(process: subprocess.Popen[str]) -> str:
    assert process.stdout is not None
    with selectors.DefaultSelector() as selector:
        selector.register(process.stdout, selectors.EVENT_READ)
        assert selector.select(TIMEOUT), "writer did not reach its controlled checkpoint"
    line = process.stdout.readline()
    assert line, "writer exited before its checkpoint"
    return line.strip()


def test_killed_writer_never_publishes_and_kernel_lock_is_reusable(tmp_path: Path) -> None:
    destination = tmp_path / "assessment"
    with worker(destination, "interrupt", "first") as process:
        staging = Path(json.loads(ready_line(process))["staging"])
        assert (staging / "partial.bin").read_bytes() == b"unfinished caller data"
        assert not destination.exists()
        process.kill()
        _, error = process.communicate(timeout=TIMEOUT)
        assert process.returncode == -signal.SIGKILL
        assert not error
    assert not destination.exists()
    assert output_lock_path(destination).is_file()
    with fresh_output(destination) as replacement:
        (replacement / "complete.txt").write_text("new completed assessment")
    assert {path.name for path in destination.iterdir()} == {"complete.txt"}
    assert (destination / "complete.txt").read_text() == "new completed assessment"
    # SIGKILL cannot run Python cleanup. This private orphan never became the
    # destination; the test owns it and removes it after checking recovery.
    assert staging.is_dir() and staging.stat().st_mode & 0o077 == 0
    shutil.rmtree(staging)


def test_two_processes_publish_one_complete_winner_without_clobber(tmp_path: Path) -> None:
    destination = tmp_path / "export"
    with (
        worker(destination, "race", "first") as first,
        worker(destination, "race", "second") as second,
    ):
        assert ready_line(first) == ready_line(second) == "READY"
        for process in (first, second):
            assert process.stdin is not None
            process.stdin.write("GO\n")
            process.stdin.flush()
        records = []
        for process in (first, second):
            output, error = process.communicate(timeout=TIMEOUT)
            assert process.returncode == 0, error
            assert not error
            records.append(json.loads(output))
    assert sorted(record["status"] for record in records) == ["FileExistsError", "published"]
    winner = next(record["label"] for record in records if record["status"] == "published")
    assert {path.name for path in destination.iterdir()} == {"winner.txt", "payload.bin"}
    assert (destination / "winner.txt").read_text() == winner
    assert (destination / "payload.bin").read_bytes() == winner.encode() * 1024
    assert not list(tmp_path.glob(".export.ipe-*"))
