# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""
I/O safety utilities for atomic writes and concurrency control.
"""

import contextlib
import os
import tempfile
import time
from collections.abc import Generator
from pathlib import Path


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
    """Acquire an exclusive lock using fcntl.flock (Linux/Unix).

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
    import errno
    import fcntl
    import random

    start_time = time.time()

    # Ensure directory exists
    lockfile.parent.mkdir(parents=True, exist_ok=True)

    # 1. Open the file (create if missing)
    # We maintain the file handle open while holding the lock
    with open(lockfile, "w", encoding="utf-8") as f:
        try:
            while True:
                try:
                    # 2. Try to acquire exclusive, non-blocking lock
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)

                    # 3. Write fencing token (for debug/human visibility only)
                    with contextlib.suppress(OSError):
                        f.seek(0)
                        f.truncate()
                        f.write(f"pid={os.getpid()}\ntimestamp={time.time()}\n")
                        f.flush()

                    yield
                    return  # Successfully yielded and finished

                except OSError as e:
                    # EWOULDBLOCK / EAGAIN means locked by another process
                    if e.errno != errno.EWOULDBLOCK and e.errno != errno.EAGAIN:
                        raise

                    # Check timeout
                    if timeout <= 0 or (time.time() - start_time) >= timeout:
                        raise BlockingIOError(f"Could not acquire lock: {lockfile}") from e

                    # Jittered backoff (poll_ms +/- 20%)
                    jitter = (random.random() - 0.5) * 0.4 * (poll_ms / 1000.0)
                    time.sleep((poll_ms / 1000.0) + jitter)
        finally:
            # Unlock and close behavior:
            # flock is removed when fd is closed.
            with contextlib.suppress(OSError):
                # Explicit unlock (good practice though close() does it)
                fcntl.flock(f, fcntl.LOCK_UN)
