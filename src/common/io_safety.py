# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""
I/O safety utilities for atomic writes and concurrency control.
"""

import contextlib
import errno
import os
import random
import tempfile
import time
from collections.abc import Generator
from pathlib import Path
from typing import Any, BinaryIO

_IS_WINDOWS = os.name == "nt"
_LOCK_BUSY_ERRNOS = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}


def _prepare_lock_file(file: BinaryIO) -> None:
    """Ensure the byte-range used by the Windows lock exists."""
    if not _IS_WINDOWS:
        return

    file.seek(0, os.SEEK_END)
    if file.tell() == 0:
        file.write(b"\0")
        file.flush()
    file.seek(0)


def _try_lock(file: BinaryIO) -> None:
    """Try once to acquire a platform-native, non-blocking exclusive lock."""
    if _IS_WINDOWS:
        import msvcrt

        file.seek(0)
        windows_api: Any = msvcrt
        windows_api.locking(file.fileno(), windows_api.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock(file: BinaryIO) -> None:
    """Release a lock acquired by :func:`_try_lock`."""
    if _IS_WINDOWS:
        import msvcrt

        file.seek(0)
        windows_api: Any = msvcrt
        windows_api.locking(file.fileno(), windows_api.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(file, fcntl.LOCK_UN)


def atomic_write_text(path: Path, text: str, encoding: str = "utf-8") -> None:
    """Write text to a file atomically using a temporary file and os.replace.

    Args:
        path: Target file path.
        text: Content to write.
        encoding: Text encoding (default utf-8).
    """
    # Ensure parent directory exists
    path.parent.mkdir(parents=True, exist_ok=True)

    # Create a temp file in the same directory to ensure os.replace works (same filesystem)
    # delete=False because we want to close it and then rename it.
    fd, tmp_path = tempfile.mkstemp(dir=path.parent, text=True)
    try:
        with os.fdopen(fd, "w", encoding=encoding) as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())

        # Atomic replace
        os.replace(tmp_path, path)
    except Exception:
        # Cleanup temp file on failure
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


@contextlib.contextmanager
def acquire_lock(
    lockfile: Path, timeout: float = 0.0, poll_ms: int = 100
) -> Generator[None, None, None]:
    """Acquire a platform-native exclusive filesystem lock.

    PROPERTIES:
    - Exclusive: Only one process holds it.
    - Atomic: OS kernel arbitration.
    - Crash-safe: Lock is released if process dies (no stale-break needed).
    - Non-blocking + Retry: Retries until timeout.

    Args:
        lockfile: Path to the lock file (NOT directory).
        timeout: Seconds to wait.
        poll_ms: Milliseconds to wait between retries.

    Yields:
        None

    Raises:
        BlockingIOError: If lock cannot be acquired within timeout.
    """
    start_time = time.monotonic()

    # Ensure directory exists
    lockfile.parent.mkdir(parents=True, exist_ok=True)

    # Keep the same file handle open while holding the lock. Append mode avoids
    # truncating a fencing token written by the process that currently owns it.
    with open(lockfile, "a+b") as f:
        _prepare_lock_file(f)
        acquired = False
        try:
            while True:
                try:
                    _try_lock(f)
                except OSError as e:
                    if e.errno not in _LOCK_BUSY_ERRNOS:
                        raise

                    if timeout <= 0 or (time.monotonic() - start_time) >= timeout:
                        raise BlockingIOError(f"Could not acquire lock: {lockfile}") from e

                    jitter = (random.random() - 0.5) * 0.4 * (poll_ms / 1000.0)
                    time.sleep(max(0.0, (poll_ms / 1000.0) + jitter))
                else:
                    acquired = True
                    break

            # Fencing token is for diagnostics only; lock ownership is enforced
            # by the operating system.
            with contextlib.suppress(OSError):
                f.seek(0)
                f.truncate()
                token = f"pid={os.getpid()}\ntimestamp={time.time()}\n".encode()
                f.write(token)
                f.flush()

            yield
        finally:
            if acquired:
                with contextlib.suppress(OSError):
                    _unlock(f)
