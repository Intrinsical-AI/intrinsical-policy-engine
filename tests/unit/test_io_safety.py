"""Tests for portable filesystem locking."""

import errno
import sys
from types import SimpleNamespace

import pytest

from src.common import io_safety


def test_acquire_lock_writes_fencing_token(tmp_path):
    lockfile = tmp_path / "state.lock"

    with io_safety.acquire_lock(lockfile):
        token = lockfile.read_text(encoding="utf-8")

    assert "pid=" in token
    assert "timestamp=" in token


def test_acquire_lock_preserves_body_blocking_error(tmp_path):
    expected = BlockingIOError(errno.EAGAIN, "body failed")

    with (
        pytest.raises(BlockingIOError) as captured,
        io_safety.acquire_lock(tmp_path / "state.lock"),
    ):
        raise expected

    assert captured.value is expected


def test_acquire_lock_uses_windows_byte_range_lock(monkeypatch, tmp_path):
    calls: list[tuple[int, int]] = []

    def locking(_fd: int, mode: int, size: int) -> None:
        calls.append((mode, size))

    fake_msvcrt = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(io_safety, "_IS_WINDOWS", True)

    with io_safety.acquire_lock(tmp_path / "state.lock"):
        pass

    assert calls == [(fake_msvcrt.LK_NBLCK, 1), (fake_msvcrt.LK_UNLCK, 1)]


def test_windows_lock_contention_raises_blocking_error(monkeypatch, tmp_path):
    def locking(_fd: int, mode: int, _size: int) -> None:
        if mode == 1:
            raise OSError(errno.EACCES, "locked")

    fake_msvcrt = SimpleNamespace(LK_NBLCK=1, LK_UNLCK=2, locking=locking)
    monkeypatch.setitem(sys.modules, "msvcrt", fake_msvcrt)
    monkeypatch.setattr(io_safety, "_IS_WINDOWS", True)

    with (
        pytest.raises(BlockingIOError, match="Could not acquire lock"),
        io_safety.acquire_lock(tmp_path / "state.lock"),
    ):
        pass
