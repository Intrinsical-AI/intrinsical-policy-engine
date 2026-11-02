# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Publish newly created outputs without replacing another owner's files."""

from __future__ import annotations

import ctypes
import errno
import os
import shutil
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from intrinsical_policy_engine.common.io_safety import acquire_lock


def output_lock_path(destination: Path) -> Path:
    """Keep the cooperative lock outside the output tree and never remove it."""
    target = destination.resolve()
    return target.parent / f".{target.name}.ipe.lock"


def require_new_output(destination: Path) -> None:
    """Reject existing files, directories, and dangling symbolic links."""
    if destination.exists() or destination.is_symlink():
        raise FileExistsError(f"Output already exists: {destination}")


def publish_file_no_replace(prepared: Path, destination: Path) -> None:
    """Expose a complete file with an atomic, exclusive directory entry.

    The caller prepares the file on the destination filesystem. A hard link
    provides no-replace semantics, including when a competing writer creates
    the destination after its initial existence check.
    """
    os.link(prepared, destination, follow_symlinks=False)
    prepared.unlink()


def _publish_directory_no_replace(prepared: Path, destination: Path) -> None:
    if os.name == "nt":
        # Windows rename fails if the destination already exists.
        os.rename(prepared, destination)
        return

    library = ctypes.CDLL(None, use_errno=True)
    rename = getattr(library, "renameat2", None)
    if rename is None:
        raise OSError(errno.ENOTSUP, "Exclusive directory publication is unavailable")
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    rename.restype = ctypes.c_int
    # AT_FDCWD / RENAME_NOREPLACE. No destructive rename fallback is safe here.
    if rename(-100, os.fsencode(prepared), -100, os.fsencode(destination), 1) != 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error), str(destination))


@contextmanager
def fresh_output(destination: Path) -> Iterator[Path]:
    """Build in a private sibling and publish a new directory when complete.

    Cooperating writers share the stable sidecar lock. The native exclusive
    rename also preserves outputs created by writers that do not take it.
    """
    if destination.is_symlink():
        raise FileExistsError(f"Output is a symbolic link: {destination}")
    target = destination.resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    with acquire_lock(output_lock_path(target)):
        require_new_output(target)
        prepared = Path(tempfile.mkdtemp(prefix=f".{target.name}.ipe-", dir=target.parent))
        try:
            yield prepared
            _publish_directory_no_replace(prepared, target)
        finally:
            if prepared.exists():
                shutil.rmtree(prepared)
