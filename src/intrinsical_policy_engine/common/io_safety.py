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
from typing import Any, BinaryIO, NoReturn

_IS_WINDOWS = os.name == "nt"
_LOCK_BUSY_ERRNOS = {errno.EACCES, errno.EAGAIN, errno.EWOULDBLOCK}


class UnsafeTreePathError(ValueError):
    """Raised when a filesystem tree contains a symbolic link or escape."""


class OutputPackPathOverlapError(UnsafeTreePathError):
    """Raised when an export output and its source pack overlap."""


def _raise_walk_error(error: OSError) -> NoReturn:
    """Make filesystem traversal fail closed instead of skipping unreadable entries."""
    raise error


def validated_tree_files(root: Path) -> list[Path]:
    """Return regular files below ``root`` without following symbolic links.

    The complete tree is validated before callers receive any paths, so callers
    can safely read the returned files without accidentally dereferencing a
    pre-existing file or directory symlink. Traversal errors fail closed.
    """
    if root.is_symlink():
        raise UnsafeTreePathError(f"Tree root must not be a symbolic link: {root}")

    resolved_root = root.resolve(strict=True)
    if not resolved_root.is_dir():
        raise NotADirectoryError(f"Tree root is not a directory: {root}")

    files: list[Path] = []
    for current, directory_names, file_names in os.walk(
        root,
        topdown=True,
        onerror=_raise_walk_error,
        followlinks=False,
    ):
        directory_names.sort()
        file_names.sort()
        current_path = Path(current)

        # os.walk does not descend into directory symlinks when followlinks is
        # false, so inspect both collections explicitly (including dangling
        # links) instead of relying on is_file()/is_dir(), which dereference.
        for name in (*directory_names, *file_names):
            candidate = current_path / name
            if candidate.is_symlink():
                raise UnsafeTreePathError(
                    f"Symbolic links are forbidden in validated trees: {candidate}"
                )

        for name in file_names:
            candidate = current_path / name
            if not candidate.is_file():
                continue
            resolved_candidate = candidate.resolve(strict=True)
            try:
                resolved_candidate.relative_to(resolved_root)
            except ValueError as exc:
                raise UnsafeTreePathError(
                    f"Tree entry escapes validated root: {candidate}"
                ) from exc
            files.append(candidate)

    return sorted(files)


def validate_export_output_boundary(pack_root: Path, output_root: Path) -> None:
    """Reject output trees that overlap a pack or contain unsafe paths.

    This function is intentionally side-effect free. Callers must invoke it
    before creating the output directory, configuring file-backed logging, or
    writing assessment/debug artifacts.
    """
    expanded_output = output_root.expanduser()
    absolute_output = Path(os.path.abspath(expanded_output))
    for component in (*reversed(absolute_output.parents), absolute_output):
        if component.is_symlink():
            raise UnsafeTreePathError(
                f"Symbolic links are forbidden in export output paths: {component}"
            )

    resolved_pack = pack_root.expanduser().resolve(strict=False)
    resolved_output = expanded_output.resolve(strict=False)
    if (
        resolved_output == resolved_pack
        or resolved_output.is_relative_to(resolved_pack)
        or resolved_pack.is_relative_to(resolved_output)
    ):
        raise OutputPackPathOverlapError(
            "Export output and framework pack directories must not overlap: "
            f"output={output_root}, pack={pack_root}"
        )

    if expanded_output.exists():
        try:
            validated_tree_files(expanded_output)
        except OSError as exc:
            raise UnsafeTreePathError(
                f"Export output tree could not be validated safely: {expanded_output}"
            ) from exc


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
