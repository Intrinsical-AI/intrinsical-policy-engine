# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Shared plumbing for exporters that generate API request payloads."""

from __future__ import annotations

import contextlib
import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any

from src.adapters.export.base.evidence.task_api_utils import redact_headers
from src.adapters.export.base.evidence.task_graph import build_task_items
from src.adapters.export.base.export_helpers import ExportHelpers
from src.adapters.logging import StructuredLogger, adapt_logger
from src.app.config.constants import DEFAULT_ENCODING, EXPORTS_DIR, REQUESTS_NDJSON


class BaseApiExporter(ExportHelpers, ABC):
    """Implements the template method to materialize API request payloads."""

    def __init__(self) -> None:
        """Initialize lazily configured logger/config placeholders."""
        self._logger: StructuredLogger | None = None
        self._config: dict[str, Any] | None = None

    def setup(
        self, logger: StructuredLogger | logging.Logger | None = None, config: dict | None = None
    ) -> None:
        """Store logger reference and normalized config for later use.

        Args:
            logger: Logger instance implementing StructuredLogger protocol, or standard
                logging.Logger (will be adapted automatically). Optional.
            config: Configuration dictionary (will be normalized via normalize_config).
        """
        self._logger = adapt_logger(logger)
        self._config = self.normalize_config(config or {})

    def export(self, plan: dict[str, Any], templates_dir: str, out_dir: str) -> None:
        """Template method for API exports.

        Implements the common pattern shared by all API exporters:
        1. Build task items from plan
        2. Setup export directory and files
        3. Process configuration and headers
        4. Validate strict mode requirements
        5. Generate API requests using abstract methods
        6. Write summary and index files

        Args:
            plan: Compliance plan dictionary with actions, metadata, etc.
            templates_dir: Path to template files (unused for API exporters).
            out_dir: Output directory where request payloads will be written.

        Raises:
            ValueError: If strict mode validation fails.
            OSError: If export directory cannot be created or files cannot be written.
        """
        target_name = self.get_target_name()
        items = build_task_items(plan)
        base = self._ensure_export_dir(out_dir, target_name)
        reqs = base / REQUESTS_NDJSON
        count = 0

        cfg = self._cfg()
        headers = self._get_headers()

        # Validate strict mode requirements
        self._validate_strict_requirements(cfg)

        self._log_safe(
            f"export.{target_name}.start",
            {"items": len(items), **self.get_log_context(cfg)},
        )

        # Atomic writes using temp file + replace pattern
        # If writing fails, temp file is removed and original remains intact
        reqs_tmp = base / f"{REQUESTS_NDJSON}.tmp"
        try:
            with reqs_tmp.open("w", encoding=DEFAULT_ENCODING) as f:
                for it in items:
                    # Build description using common helper
                    desc = self.build_description(it.description_md, out_dir)

                    # Build request using abstract method
                    rec = self.build_request_record(it, desc, cfg, headers)
                    f.write(json.dumps(rec) + "\n")
                    count += 1
            # Atomic replace: if we get here, all writes succeeded
            reqs_tmp.replace(reqs)
        except Exception:
            # Cleanup temp file on any error
            with contextlib.suppress(OSError):
                reqs_tmp.unlink()
            raise

        # Write summary and index
        summary = self.evidence_summary(out_dir)
        self.write_index(base, {"target": target_name, "count": count, **summary})

        self._log_safe(
            f"export.{target_name}.finish",
            {"count": count, **self.get_log_context(cfg)},
        )

    @abstractmethod
    def get_target_name(self) -> str:
        """Return the target name for this exporter.

        Returns:
            Canonical target name (e.g., 'asana', 'jira', 'linear').
        """
        pass

    @abstractmethod
    def build_request_record(
        self, item: Any, description: str, cfg: dict, headers: dict[str, str]
    ) -> dict[str, Any]:
        """Build the API request record for a single task item.

        Subclasses must implement this to create API-specific request payloads.

        Args:
            item: TaskItem object with action metadata.
            description: Markdown description for the task.
            cfg: Normalized exporter configuration.
            headers: HTTP headers dictionary (may be redacted).

        Returns:
            Dictionary with API request structure (method, url, body, etc.).
        """
        pass

    def _validate_strict_requirements(self, cfg: dict) -> None:
        """Validate strict mode requirements. Override in subclasses."""
        if self._is_strict():
            self._validate_strict_config(cfg)

    def _validate_strict_config(self, cfg: dict) -> None:
        """Override in subclasses to validate specific strict requirements."""
        pass

    def _require_config_value(self, cfg: dict, key: str, target_name: str) -> Any:
        """Helper to require a config value in strict mode.

        Args:
            cfg: Configuration dictionary.
            key: Key to require.
            target_name: Exporter target name (for error messages).

        Returns:
            The config value if present.

        Raises:
            ValueError: If key is missing or empty (only in strict mode).
        """
        value = cfg.get(key)
        if not value:
            raise ValueError(f"{target_name}: config.{key} is required in strict mode")
        return value

    def get_log_context(self, cfg: dict) -> dict[str, Any]:
        """Get context data for logging. Override in subclasses.

        Args:
            cfg: Normalized exporter configuration.

        Returns:
            Dictionary with context data for structured logging.
        """
        return {}

    def _cfg(self) -> dict:
        """Return the current normalized exporter config."""
        return getattr(self, "_config", {}) or {}

    def _ensure_export_dir(self, out_dir: str, target: str) -> Path:
        """Ensure export directory exists and return Path object.

        Args:
            out_dir: Base output directory path
            target: Export target name (e.g., 'asana', 'jira', 'linear')

        Returns:
            Path object for the ensured export directory
        """
        base = Path(out_dir) / EXPORTS_DIR / target
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _log_safe(self, event: str, data: dict) -> None:
        """Log event safely with error handling.

        Args:
            event: Event name for logging
            data: Data dictionary to include in log
        """
        logger: StructuredLogger | None = getattr(self, "_logger", None)
        if logger is not None:
            with contextlib.suppress(AttributeError, OSError):
                logger.info(event, data)

    def _is_strict(self) -> bool:
        """Check if strict mode is enabled in config.

        Strict mode is enabled by default for production safety. This ensures that:
        - Required configuration values (base_url, project keys, etc.) are validated
        - Missing templates or evidence trigger errors instead of silent fallbacks
        - Configuration inconsistencies are caught early

        To disable strict mode, explicitly set config["strict"] = False.

        Returns:
            True if strict mode is enabled (default), False if explicitly disabled
        """
        cfg = self._cfg()
        return bool(cfg.get("strict", True))

    def _get_headers(self) -> dict[str, str]:
        """Get redacted headers from config with fallback.

        Returns:
            Dictionary of redacted headers, with default Authorization fallback
        """
        cfg = self._cfg()
        return redact_headers(cfg.get("headers")) or {"Authorization": "Bearer <redacted>"}
