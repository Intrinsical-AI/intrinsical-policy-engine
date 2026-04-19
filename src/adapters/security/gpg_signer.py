# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""GPG signing adapter implementing SigningPort."""

import logging
import shutil
import subprocess
from pathlib import Path

from src.domain.ports import SigningPort

logger = logging.getLogger(__name__)


class GpgSigner(SigningPort):
    """Adaptador que invoca al binario 'gpg' del sistema."""

    def __init__(self, key_id: str | None = None):
        self._gpg_path = shutil.which("gpg") or shutil.which("gpg2")
        self._key_id = key_id

    def is_available(self) -> bool:
        return self._gpg_path is not None

    def has_secret_key(self) -> bool:
        """Check if at least one secret key is available for signing.

        Red Team R4 Fix: is_available() only checks for GPG binary.
        This method checks if there's actually a key to sign with.
        """
        if not self.is_available():
            return False

        gpg_path = self._gpg_path
        if gpg_path is None:
            return False

        try:
            result = subprocess.run(
                [gpg_path, "--list-secret-keys", "--keyid-format", "short"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            # GPG returns exit code 0 if there are keys, and output contains key info
            return result.returncode == 0 and "sec" in result.stdout
        except (subprocess.SubprocessError, OSError):
            return False

    def sign_file(self, path: Path) -> Path | None:
        if not self.is_available():
            logger.warning("GPG not found. Skipping signature for %s", path.name)
            return None

        gpg_path = self._gpg_path
        if gpg_path is None:
            logger.warning("GPG path is not set. Skipping signature for %s", path.name)
            return None

        # Output file: path.asc (Armor ASCII)
        sig_path = path.with_suffix(path.suffix + ".asc")

        cmd: list[str] = [
            gpg_path,
            "--batch",
            "--yes",  # No interactivo, sobrescribir
            "--detach-sign",  # Firma desvinculada (el archivo original se mantiene)
            "--armor",  # Salida ASCII (más amigable para git/email)
            "--output",
            str(sig_path),
            str(path),
        ]

        key_id = self._key_id
        if key_id:
            cmd.extend(["--local-user", key_id])

        try:
            logger.info("Signing %s with GPG...", path.name)
            subprocess.run(cmd, check=True, capture_output=True, text=True)
            return sig_path
        except subprocess.CalledProcessError as e:
            logger.error(
                "GPG signing failed for %s. Code: %s. Stderr: %s",
                path.name,
                e.returncode,
                e.stderr.strip(),
            )
            return None
        except OSError as e:
            logger.error("OS error executing GPG: %s", e)
            return None
