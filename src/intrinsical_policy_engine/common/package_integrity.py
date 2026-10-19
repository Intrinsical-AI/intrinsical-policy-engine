# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""One inventory and checksum format for exported, sealed, and zipped bytes."""

from __future__ import annotations

import hashlib
import os
import re
import stat
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from intrinsical_policy_engine.common.output_safety import publish_file_no_replace
from intrinsical_policy_engine.domain.snapshot_contract import (
    CHECKSUMS_NAME,
    CONTROL_PATHS,
    REQUIRED_METADATA,
    PackageIntegrityError,
    canonical_path,
)

_CHECKSUM_LINE = re.compile(r"([0-9a-f]{64})  (.+)")


@contextmanager
def open_regular(path: Path) -> Iterator[BinaryIO]:
    """Reject links and special files, including a substituted final symlink."""
    if path.is_symlink():
        raise PackageIntegrityError(f"Symbolic link is not package content: {path}")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags)
    with os.fdopen(descriptor, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise PackageIntegrityError(f"Not a regular package file: {path}")
        yield stream


def read_regular(path: Path) -> bytes:
    with open_regular(path) as stream:
        return stream.read()


def hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open_regular(path) as stream:
        before = os.fstat(stream.fileno())
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
        after = os.fstat(stream.fileno())
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise PackageIntegrityError(f"File changed during hashing: {path}")
    return digest.hexdigest()


def payload_paths(root: Path) -> tuple[str, ...]:
    """Enumerate every delivered file; only the two root controls are separate."""
    if root.is_symlink() or not root.is_dir():
        raise PackageIntegrityError(f"Package directory not found or unsafe: {root}")
    paths: list[str] = []
    for candidate in root.rglob("*"):
        rel = canonical_path(candidate.relative_to(root).as_posix())
        if candidate.is_symlink():
            raise PackageIntegrityError(f"Symbolic link is not package content: {rel}")
        if candidate.is_dir():
            continue
        if not candidate.is_file():
            raise PackageIntegrityError(f"Not a regular package file: {rel}")
        if rel not in CONTROL_PATHS:
            paths.append(rel)
    return tuple(sorted(paths))


def scan_payload(root: Path) -> dict[str, str]:
    return {rel: hash_file(root / rel) for rel in payload_paths(root)}


def parse_checksums(content: bytes) -> dict[str, str]:
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise PackageIntegrityError("CHECKSUMS is not UTF-8") from exc
    result: dict[str, str] = {}
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    for number, line in enumerate(lines, 1):
        match = _CHECKSUM_LINE.fullmatch(line)
        if match is None:
            raise PackageIntegrityError(f"Malformed CHECKSUMS line {number}")
        digest, rel = match.groups()
        canonical_path(rel)
        if rel in CONTROL_PATHS or rel in result:
            raise PackageIntegrityError(f"Duplicate or control path in CHECKSUMS: {rel}")
        result[rel] = digest
    if not result:
        raise PackageIntegrityError("CHECKSUMS is empty")
    return result


def load_checksums(root: Path) -> tuple[bytes, dict[str, str]]:
    content = read_regular(root / CHECKSUMS_NAME)
    return content, parse_checksums(content)


def verify_payload(root: Path, expected: dict[str, str]) -> None:
    actual = scan_payload(root)
    if actual != expected:
        added = sorted(actual.keys() - expected.keys())
        removed = sorted(expected.keys() - actual.keys())
        modified = sorted(
            path for path in actual.keys() & expected.keys() if actual[path] != expected[path]
        )
        raise PackageIntegrityError(
            f"Package drift: added={added}, removed={removed}, modified={modified}"
        )


def write_checksums(root: Path) -> Path:
    """Finalize the export after every payload writer has finished."""
    for rel in CONTROL_PATHS:
        if (root / rel).exists() or (root / rel).is_symlink():
            raise PackageIntegrityError(f"Export already has an integrity control: {rel}")
    checksums = scan_payload(root)
    missing = REQUIRED_METADATA - checksums.keys()
    if missing:
        raise PackageIntegrityError(f"Required metadata missing: {sorted(missing)}")
    content = "".join(f"{digest}  {rel}\n" for rel, digest in checksums.items()).encode("utf-8")
    descriptor, name = tempfile.mkstemp(prefix=".checksums-", dir=root.parent)
    prepared = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        verify_payload(root, checksums)
        publish_file_no_replace(prepared, root / CHECKSUMS_NAME)
    finally:
        prepared.unlink(missing_ok=True)
    return root / CHECKSUMS_NAME
