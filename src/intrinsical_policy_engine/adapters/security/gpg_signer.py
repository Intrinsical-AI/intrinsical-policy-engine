# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Bounded local GPG signing and verification of an exact checksum baseline."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


class SigningError(ValueError):
    """Signing or cryptographic verification did not complete successfully."""


class GpgSigner:
    def __init__(self, key_id: str | None = None, *, timeout: float = 30.0):
        self._gpg_path = shutil.which("gpg") or shutil.which("gpg2")
        self._key_id = key_id
        self._timeout = timeout

    def _run(self, arguments: list[str]) -> subprocess.CompletedProcess[str]:
        if self._gpg_path is None:
            raise SigningError("GPG is unavailable; use --no-sign for an unsigned package")
        try:
            result = subprocess.run(
                [self._gpg_path, "--batch", "--no-auto-key-retrieve", *arguments],
                capture_output=True,
                text=True,
                timeout=self._timeout,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise SigningError(f"GPG did not complete: {exc}") from exc
        if result.returncode != 0:
            raise SigningError(f"GPG failed: {result.stderr.strip()}")
        return result

    def sign_file(self, path: Path) -> Path:
        """Create a detached signature only at the caller's private staging path."""
        signature = path.with_name(path.name + ".asc")
        if signature.exists() or signature.is_symlink():
            raise SigningError(f"Signature destination already exists: {signature}")
        arguments = ["--armor", "--detach-sign", "--output", str(signature)]
        if self._key_id:
            arguments.extend(["--local-user", self._key_id])
        arguments.extend(["--", str(path)])
        try:
            self._run(arguments)
            if signature.is_symlink() or not signature.is_file():
                raise SigningError("GPG did not create a regular signature file")
        except SigningError:
            signature.unlink(missing_ok=True)
            raise
        return signature

    def verify_file(self, path: Path, signature: Path) -> str:
        """Return a valid signature's fingerprint; never fetch keys online."""
        result = self._run(["--status-fd", "1", "--verify", str(signature), str(path)])
        fingerprints = []
        for line in result.stdout.splitlines():
            fields = line.split()
            if len(fields) >= 3 and fields[:2] == ["[GNUPG:]", "VALIDSIG"]:
                fingerprints.append(fields[2])
        if len(fingerprints) != 1:
            raise SigningError("GPG did not report exactly one valid signature")
        return fingerprints[0]
