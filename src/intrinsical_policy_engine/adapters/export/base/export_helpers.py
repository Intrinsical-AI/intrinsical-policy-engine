# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Utility mixin shared by exporters for writing files and composing payloads."""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import Any

from intrinsical_policy_engine.adapters.export.base.models.config_constants import ExporterConfig
from intrinsical_policy_engine.adapters.logging import StructuredLogger
from intrinsical_policy_engine.app.config.constants import EVIDENCE_ZIP, ICS_FILE, INDEX_JSON


class ExportHelpers:
    """Provide reusable helpers covering logging, IO, and config handling."""

    _config: dict[str, Any] | None
    _logger: StructuredLogger | None

    def write_text(self, path: Path, content: str, base_dir: Path | str | None = None) -> None:
        """Write text to file with strict path traversal protection.

        Args:
            path: Target file path (can be relative)
            content: Text content to write
            base_dir: Optional base directory to restrict writes to.
                     If provided, writes outside this dir will raise ExportPathError.
        """
        # Ensure path is a Path object
        path_obj = Path(path) if not isinstance(path, Path) else path

        if base_dir:
            from intrinsical_policy_engine.domain.exceptions import ExportPathError

            base = Path(base_dir).resolve()
            # Resolve target path relative to base if it's relative, or absolute
            # Note: path might be absolute but inside base, or relative
            target = path_obj.resolve() if path_obj.is_absolute() else (base / path_obj).resolve()

            if not target.is_relative_to(base):
                # Log the attempt (using internal logger if available)
                self.log_warning(
                    "Blocked path traversal attempt",
                    {"target": str(target), "base_dir": str(base)},
                )
                raise ExportPathError(f"Path traversal detected: {target} is outside {base}")

            # Use the resolved target path for writing
            final_path = target
        else:
            # Resolve path even when base_dir is None to ensure parent directory exists
            final_path = path_obj.resolve()

        final_path.parent.mkdir(parents=True, exist_ok=True)
        final_path.write_text(content, encoding="utf-8", newline="\n")

    def write_json(
        self, path: Path, obj: dict[str, Any], base_dir: Path | str | None = None
    ) -> None:
        """Serialize a mapping to JSON while reusing the safeguarded writer.

        Args:
            path: Target file path (can be relative).
            obj: Dictionary to serialize to JSON.
            base_dir: Optional base directory to restrict writes to.
                If provided, writes outside this dir will raise ExportPathError.

        Raises:
            ExportPathError: If base_dir is provided and path is outside it.
            OSError: If file cannot be written.
        """
        self.write_text(path, json.dumps(obj, indent=2, ensure_ascii=False), base_dir=base_dir)

    def append_manifest_lines(self, out_dir: str) -> list[str]:
        """Read rendered evidence manifest lines for inclusion in descriptions."""
        from intrinsical_policy_engine.adapters.export.base.evidence.task_api_utils import (
            read_manifest_lines,
        )

        return read_manifest_lines(out_dir)

    def evidence_summary(self, out_dir: str | Path) -> dict[str, Any]:
        """Return evidence manifest summary counts.

        Args:
            out_dir: Export output directory containing evidence manifest.

        Returns:
            Dictionary with evidence summary statistics (file counts, etc.).
        """
        from intrinsical_policy_engine.adapters.export.base.evidence.evidence_utils import (
            evidence_summary as _evidence_summary,
        )

        return _evidence_summary(out_dir)

    def write_index(self, base: Path, data: dict[str, Any]) -> None:
        """Persist exporter index metadata (target, counts, evidence).

        Args:
            base: Base directory where index.json will be written.
            data: Dictionary containing exporter metadata (target name, counts, etc.).
        """
        self.write_json(base / INDEX_JSON, data)

    def attachments_lines(self, out_dir: str) -> list[str]:
        """Build markdown lines with download links when a file host is configured.

        Checks config for public_base_url. If found, generates markdown links to
        evidence_zip and compliance_ics files.

        Args:
            out_dir: Export output directory (unused, kept for API compatibility).

        Returns:
            List of markdown-formatted lines with attachment links, or empty list
            if no file host is configured.
        """
        cfg = getattr(self, "_config", {}) or {}
        base = cfg.get(ExporterConfig.PUBLIC_BASE_URL)
        if not base:
            return []

        def _join(u: str, name: str) -> str:
            return (u if u.endswith("/") else u + "/") + name

        return [
            "",
            "## Attachments",
            f"- evidence_zip: {_join(base, EVIDENCE_ZIP)}",
            f"- compliance_ics: {_join(base, ICS_FILE)}",
        ]

    def quality_lines(self, out_dir: str) -> list[str]:
        """Load markdown lines describing evidence quality analysis.

        Args:
            out_dir: Export output directory containing quality report files.

        Returns:
            List of markdown-formatted lines describing evidence quality metrics.
        """
        from intrinsical_policy_engine.adapters.export.base.evidence.task_api_utils import (
            read_quality_lines,
        )

        return read_quality_lines(out_dir)

    def log_info(self, event: str, data: dict) -> None:
        """Best-effort info logging helper that tolerates logger issues."""
        logger: StructuredLogger | None = getattr(self, "_logger", None)
        if logger is not None:
            with contextlib.suppress(AttributeError, OSError):
                logger.info(event, data)

    def log_warning(self, event: str, data: dict) -> None:
        """Best-effort warning logging helper that tolerates logger issues."""
        logger: StructuredLogger | None = getattr(self, "_logger", None)
        if logger is not None:
            with contextlib.suppress(AttributeError, OSError):
                logger.warning(event, data)

    def compose_description(
        self, base_description: str, manifest_lines: list[str], attach_lines: list[str]
    ) -> str:
        """Merge base description with optional manifest/attachment details.

        Args:
            base_description: Base markdown description text.
            manifest_lines: Optional list of manifest markdown lines.
            attach_lines: Optional list of attachment markdown lines.

        Returns:
            Combined markdown string with all sections concatenated.
        """
        desc = base_description
        if manifest_lines:
            desc = desc + "\n" + "\n".join(manifest_lines)
        if attach_lines:
            desc = desc + "\n" + "\n".join(attach_lines)
        return desc

    def build_common_record(
        self,
        *,
        method: str,
        url: str,
        base_url: str | None,
        headers: dict,
        body: Any,
        uid: str,
    ) -> dict:
        """Return the canonical request payload shape used by API exporters.

        Args:
            method: HTTP method (e.g., 'POST', 'PUT').
            url: Request URL path (relative to base_url).
            base_url: Base URL for the API endpoint.
            headers: HTTP headers dictionary.
            body: Request body payload (any JSON-serializable type).
            uid: Unique identifier for the request (also used as idempotency_key).

        Returns:
            Dictionary with standardized request record structure:
            - method, url, base_url, headers, body
            - uid, idempotency_key, externalId (all set to uid)
        """
        return {
            "method": method,
            "url": url,
            "base_url": base_url,
            "headers": headers,
            "body": body,
            "uid": uid,
            "idempotency_key": uid,
            "externalId": uid,
        }

    def build_description(self, base_md: str, out_dir: str) -> str:
        """Compose the Markdown description with manifest, quality, and attachment info.

        Convenience method that combines base description with evidence manifest,
        quality report, and attachment links.

        Args:
            base_md: Base markdown description text.
            out_dir: Export output directory containing manifest and quality files.

        Returns:
            Combined markdown string with all sections.
        """
        m = self.append_manifest_lines(out_dir)
        q = self.quality_lines(out_dir)
        a = self.attachments_lines(out_dir)
        return self.compose_description(base_md, list(m) + list(q), a)

    # --- strict-mode helpers ---
    def require(self, cfg: dict, key: str, msg: str):
        """Ensure config contains a non-empty value; raise ValueError otherwise.

        Args:
            cfg: Configuration dictionary.
            key: Key to check in config.
            msg: Error message to raise if value is missing or empty.

        Returns:
            The config value if present and non-empty.

        Raises:
            ValueError: If key is missing or value is empty/None.
        """
        val = cfg.get(key) if isinstance(cfg, dict) else None
        if val in (None, "", [], {}):
            raise ValueError(msg)
        return val

    # --- label/tag mapping helpers ---
    def map_labels_to_ids(
        self,
        labels: list[str],
        label_map: dict[str, str],
        extra_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Map labels to IDs using provided mapping and extra IDs.

        Args:
            labels: List of label strings to map
            label_map: Dictionary mapping label names to IDs
            extra_ids: Additional IDs to always include

        Returns:
            Dictionary with mapping statistics and resulting ID list:
            - mapped_ids: List of mapped IDs
            - stats: Dictionary with total, mapped, unmapped counts
        """
        extra_ids = extra_ids or []
        mapped_ids: list[str] = []
        stats = {"total": 0, "mapped": 0, "unmapped": 0, "extras_applied": 0}

        for label in labels:
            stats["total"] += 1
            mapped_id = label_map.get(label)
            if mapped_id:
                if mapped_id not in mapped_ids:
                    mapped_ids.append(str(mapped_id))
                    stats["mapped"] += 1
            else:
                stats["unmapped"] += 1

        # Add extra IDs
        for extra_id in extra_ids:
            if str(extra_id) not in mapped_ids:
                mapped_ids.append(str(extra_id))
                stats["extras_applied"] += 1

        return {"mapped_ids": mapped_ids, "stats": stats}

    # --- config normalization ---
    def normalize_config(self, cfg: dict | None) -> dict:
        """Normalize exporter config keys to snake_case for consistent usage.

        Converts config keys to snake_case and normalizes case.

        Args:
            cfg: Configuration dictionary (may have mixed case keys).

        Returns:
            Normalized dictionary with snake_case keys only.
            Returns empty dict if cfg is None or not a dict.
        """
        if not isinstance(cfg, dict):
            return {}

        def to_snake(s: str) -> str:
            s = s.replace("-", "_")
            # Insert underscores before camelCase capitals when appropriate
            out = []
            for i, ch in enumerate(s):
                if (
                    ch.isupper()
                    and i > 0
                    and (s[i - 1].islower() or (i + 1 < len(s) and s[i + 1].islower()))
                ):
                    out.append("_")
                out.append(ch.lower())
            return "".join(out)

        out: dict[str, Any] = {}
        for k, v in cfg.items():
            out[to_snake(str(k))] = v
        return out
