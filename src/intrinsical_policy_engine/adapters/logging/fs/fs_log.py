# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Minimal JSONL logger for reproducible CLI/automation runs."""

from __future__ import annotations

import errno
import json
import os
import stat
from datetime import UTC, datetime
from io import BufferedRandom
from pathlib import Path
from typing import Any
from uuid import uuid4

from intrinsical_policy_engine.common.constants import DEFAULT_ENCODING
from intrinsical_policy_engine.common.document_io import parse_document
from intrinsical_policy_engine.common.io_safety import acquire_lock


def _append_separator(stream: BufferedRandom) -> bytes:
    """Separate a valid final record; refuse to append after a truncated one."""
    size = stream.seek(0, os.SEEK_END)
    if not size:
        return b""
    stream.seek(size - 1)
    if stream.read(1) == b"\n":
        return b""
    parts: list[bytes] = []
    cursor = size
    while cursor:
        start = max(0, cursor - 65536)
        stream.seek(start)
        block = stream.read(cursor - start)
        boundary = block.rfind(b"\n")
        parts.append(block[boundary + 1 :])
        if boundary >= 0:
            break
        cursor = start
    try:
        record = parse_document(b"".join(reversed(parts)).decode("utf-8"), ".json")
        if not isinstance(record, dict):
            raise ValueError("Log records must be objects")
        json.dumps(record, allow_nan=False)
    except (ValueError, UnicodeError) as error:
        raise OSError(errno.EINVAL, "Invalid trailing JSONL record") from error
    return b"\n"


class FsLogger:
    """Append complete JSON records, correlating one instance with one run."""

    def __init__(self, path: str | Path, *, command: str | None = None) -> None:
        """Keep the original path so opening it can reject symbolic links."""
        self.path = Path(path)
        self.command = command
        self.run_id = str(uuid4())
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            raise OSError(
                error.errno, f"Cannot prepare operational log: {error.strerror}", str(self.path)
            ) from error

    def _write(self, level: str, event: str, extra: dict[str, Any] | None = None) -> None:
        """Write a structured record with ISO timestamp and metadata."""
        rec = {
            "timestamp": datetime.now(UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z"),
            "run_id": self.run_id,
            "level": level,
            "event": event,
            "data": extra if extra is not None else {},
        }
        if self.command is not None:
            rec["command"] = self.command
        try:
            payload = (
                json.dumps(rec, ensure_ascii=False, allow_nan=False, separators=(",", ":")) + "\n"
            ).encode(DEFAULT_ENCODING)
        except (TypeError, ValueError) as error:
            raise ValueError(f"Cannot serialize log event {event}: {error}") from error

        # The adjacent lock coordinates appenders without locking an output
        # artifact. O_APPEND preserves prior invocations; O_NONBLOCK prevents
        # accidentally opening a FIFO from waiting for another process.
        lock_path = self.path.with_name(f".{self.path.name}.lock")
        try:
            with acquire_lock(lock_path, timeout=5.0):
                if self.path.is_symlink():
                    raise OSError(errno.ELOOP, "Symbolic link log file", str(self.path))
                flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
                flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
                descriptor = os.open(self.path, flags, 0o600)
                with os.fdopen(descriptor, "r+b") as file:
                    state = os.fstat(file.fileno())
                    if not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
                        raise OSError(errno.EINVAL, "Unsafe log file", str(self.path))
                    separator = _append_separator(file)
                    file.seek(0, os.SEEK_END)
                    file.write(separator + payload)
                    file.flush()
        except OSError as error:
            raise OSError(
                error.errno,
                f"Cannot append operational log: {error.strerror or error}",
                str(self.path),
            ) from error

    def info(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log an INFO-level record.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._write("INFO", event, extra)

    def warning(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log a WARNING-level record.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._write("WARNING", event, extra)

    def error(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log an ERROR-level record.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._write("ERROR", event, extra)

    def debug(self, event: str, extra: dict[str, Any] | None = None) -> None:
        """Log a DEBUG-level record.

        Args:
            event: Event name/identifier string.
            extra: Optional dictionary with additional context data.
        """
        self._write("DEBUG", event, extra)
