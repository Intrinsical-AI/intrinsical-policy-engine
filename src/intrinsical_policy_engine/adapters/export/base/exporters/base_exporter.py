# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Base exporter class for generating compliance artifacts and evidence packages.

This module provides the BaseExporter class which serves as the foundation for
generating various compliance artifacts such as evidence packages, reports,
and calendar events based on AI Act requirements.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import zipfile
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, TypedDict, cast

from intrinsical_policy_engine.adapters.export.base.evidence.evidence_utils import (
    load_evidence_map as _load_ev_map,
)
from intrinsical_policy_engine.adapters.export.base.export_helpers import ExportHelpers
from intrinsical_policy_engine.adapters.frameworks.layout_loader import (
    load_framework_layout_from_path,
)
from intrinsical_policy_engine.adapters.logging import StructuredLogger, adapt_logger
from intrinsical_policy_engine.app.config.constants import (
    EVIDENCE_MANIFEST,
    EVIDENCE_ZIP,
    EXPORTS_DIR,
    ICS_FILE,
)
from intrinsical_policy_engine.app.config.context import build_base_context, now_iso_z
from intrinsical_policy_engine.domain.exceptions import FingerprintError, InvalidFileError
from intrinsical_policy_engine.domain.types import Plan

# Type aliases
FilePath = str | Path


class EvidenceManifest(TypedDict, total=False):
    """Type definition for evidence manifest structure."""

    root: str
    root_abs: str
    selected_articles: list[str]
    # Mapping of article ID -> list of normalized template path strings
    by_article: dict[str, list[str]]
    included: list[str]
    missing: list[dict[str, str]]


class BaseExporter(ExportHelpers):
    """Base class for exporting compliance artifacts and evidence packages.

    This class provides common functionality for generating compliance artifacts
    such as evidence packages, reports, and calendar events based on AI Act requirements.
    """

    def __init__(self) -> None:
        """Initialize the BaseExporter with default logger and config."""
        self._logger: StructuredLogger | None = None
        self._config: dict[str, Any] = {}

    @staticmethod
    def serializable_evidence_manifest(manifest: Mapping[str, Any]) -> dict[str, Any]:
        """Return the portable manifest without process-local filesystem paths."""
        return {key: value for key, value in manifest.items() if key != "root_abs"}

    def setup(
        self,
        logger: StructuredLogger | logging.Logger | None = None,
        config: dict[str, Any] | None = None,
    ) -> None:
        """Configure the exporter with logger and configuration.

        Args:
            logger: Logger instance implementing StructuredLogger protocol, or standard
                logging.Logger (will be adapted automatically). If None, logging is disabled.
            config: Configuration dictionary for the exporter.
        """
        self._logger = adapt_logger(logger)
        self._config = self.normalize_config(config or {})
        self._log_debug("Exporter initialized", {"config_keys": list(self._config.keys())})

    # Logic moved to intrinsical_policy_engine.domain.services.metrics.py
    # This class now relies on 'metrics' key in Plan.

    # Private helper methods for logging
    def _log_debug(self, message: str, data: dict | None = None) -> None:
        """Log a debug message with structured data."""
        if self._logger is not None:
            self._logger.debug(message, data or {})

    def _log_info(self, message: str, data: dict | None = None) -> None:
        """Log an info message with structured data."""
        if self._logger is not None:
            self._logger.info(message, data or {})

    def _log_warning(self, message: str, data: dict | None = None) -> None:
        """Log a warning message with structured data."""
        if self._logger is not None:
            self._logger.warning(message, data or {})

    def _log_error(self, message: str, data: dict | None = None) -> None:
        """Log an error message with structured data."""
        if self._logger is not None:
            self._logger.error(message, data or {})

    def build_context(self, plan: Plan) -> dict[str, Any]:
        """Build a context dictionary for template rendering with metrics and plan data.

        This method combines the original plan data with computed metrics and other
        contextual information needed for template rendering.

        Args:
            plan: Plan TypedDict containing the compliance plan data.

        Returns:
            Dictionary containing the combined context with:
            - Original plan data
            - Computed metrics (coverage, HITL index, etc.)
            - Any additional context from build_base_context

        Raises:
            ValueError: If the input plan is invalid or missing required data
            TypeError: If the input is not a dictionary

        Example:
            >>> context = exporter.build_context({
            ...     "actions": ["act1", "act2"],
            ...     "articles_overlay": {"TOPIC-1": ["act1"], "TOPIC-2": ["act2"]},
            ...     "actions_meta": [{"id": "act1", "legal_refs": ["ref1"]}]
            ... })
            >>> "metrics" in context
            True
        """
        self._log_debug(
            "Building context from plan",
            {"plan_keys": list(plan.keys()) if isinstance(plan, dict) else []},
        )

        if not isinstance(plan, dict):
            error_msg = f"Plan must be a dictionary, got {type(plan).__name__}"
            self._log_error(error_msg)
            raise TypeError(error_msg)

        try:
            # Make a shallow copy to avoid modifying the input
            base_plan = cast(Plan, dict(plan) if plan else {})

            # Use pre-computed metrics from plan
            # Fallback to empty if not present (logic is now in domain service)
            metrics_map = {"metrics": base_plan.get("metrics") or {}}

            plan_with_metrics = {**base_plan, **(metrics_map or {})}

            # Build the final context
            context = build_base_context(plan_with_metrics)

            self._log_debug(
                "Context built successfully",
                {"context_keys": list(context.keys()), "has_metrics": "metrics" in context},
            )

            return context

        except Exception as e:
            self._log_error(
                "Failed to build context",
                {
                    "error": str(e),
                    "plan_type": type(plan).__name__,
                    "plan_keys": list(plan.keys()) if hasattr(plan, "keys") else [],
                },
            )
            raise ValueError(f"Failed to build context: {e}") from e

    def make_fingerprint(self, files: Iterable[Path | str]) -> dict[str, Any]:
        """Compute a deterministic fingerprint over file contents and list relative inputs.

        This method generates a SHA-256 hash of the concatenated contents of all input files,
        along with metadata about the files included in the fingerprint. The fingerprint is
        deterministic and will be the same for the same set of files, regardless of the
        order of the input.

        Args:
            files: An iterable of file paths (as Path objects or strings) to include in the
                   fingerprint. Paths can be absolute or relative, and will be converted
                   to absolute paths for processing.

        Returns:
            A dictionary containing:
            - algorithm: The hashing algorithm used (always "SHA256")
            - created_at: ISO 8601 timestamp of when the fingerprint was generated
            - digest: The hexadecimal digest of the hash
            - inputs: Sorted list of relative paths used in the fingerprint

        Raises:
            ValueError: If no files are provided or if any file cannot be read
            OSError: If there are issues accessing the files
            TypeError: If the input contains invalid types

        Example:
            >>> files = ["file1.txt", "path/to/file2.txt"]
            >>> fingerprint = exporter.make_fingerprint(files)
            >>> sorted(fingerprint.keys())
            ['algorithm', 'created_at', 'digest', 'inputs']
            >>> len(fingerprint['digest'])  # SHA-256 produces 64-character hex string
            64
        """
        # Materialize iterator once to prevent consumption (Audit BUG-1)
        files_list = list(files)

        if not files_list:
            error_msg = "Cannot create fingerprint: No files provided"
            self._log_error(error_msg)
            raise FingerprintError(error_msg)

        self._log_debug("Generating file fingerprint", {"file_count": len(files_list)})

        try:
            # Convert all inputs to Path objects and resolve to absolute paths
            paths = [Path(p).resolve() for p in files_list]

            # Ensure all paths exist and are files
            for path in paths:
                if not path.is_file():
                    reason = "file does not exist" if not path.exists() else "not a file"
                    self._log_error(
                        f"Cannot read file for fingerprint: {path}",
                        {"path": str(path), "exists": path.exists()},
                    )
                    raise InvalidFileError(file_path=str(path), reason=reason)

            # Find common parent directory if possible
            common_root = self._find_common_path(paths)

            # Generate paths for output (relative if possible, absolute if not)
            if common_root is not None:
                self._log_debug("Using relative paths", {"common_root": str(common_root)})
                rel_paths = [p.relative_to(common_root).as_posix() for p in paths]

                def sort_key(p):
                    return p.relative_to(common_root).as_posix()
            else:
                self._log_debug("Using absolute paths", {"reason": "no_common_root"})
                rel_paths = [p.as_posix() for p in paths]

                def sort_key(p):
                    return p.as_posix()

            rel_paths_sorted = sorted(rel_paths)  # Ensure consistent ordering

            # Compute hash of file contents with path+size delimiters
            # P1.4: Include path and size as boundary markers before each file
            # to prevent theoretical collisions from content concatenation
            hash_obj = hashlib.sha256()
            for path in sorted(paths, key=sort_key):
                try:
                    # Get file size for delimiter
                    file_size = path.stat().st_size
                    rel_path = (
                        path.relative_to(common_root).as_posix() if common_root else path.as_posix()
                    )
                    # Add path+size delimiter (null-separated for unambiguous parsing)
                    delimiter = f"{rel_path}\0{file_size}\0".encode()
                    hash_obj.update(delimiter)

                    with open(path, "rb") as f:
                        while chunk := f.read(8192):  # Read in chunks for memory efficiency
                            hash_obj.update(chunk)
                except OSError as e:
                    error_msg = f"Error reading file for fingerprint: {path}"
                    self._log_error(error_msg, {"error": str(e), "path": str(path)})
                    raise OSError(f"{error_msg}: {e}") from e

            # Prepare the result
            fingerprint = {
                "algorithm": "SHA256",
                "created_at": now_iso_z(),
                "digest": hash_obj.hexdigest(),
                "inputs": rel_paths_sorted,
            }

            self._log_debug(
                "Generated fingerprint",
                {
                    "file_count": len(paths),
                    "common_root": str(common_root) if common_root else "<absolute>",
                    "digest_start": fingerprint["digest"][:8],  # Just log first 8 chars of hash
                },
            )

            return fingerprint

        except Exception as e:
            self._log_error(
                "Failed to generate fingerprint",
                {
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "file_count": len(list(files)) if hasattr(files, "__len__") else "unknown",
                },
            )
            raise

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

    def _find_common_path(self, paths: list[Path]) -> Path | None:
        """Find the deepest common directory path from a list of file paths.

        Args:
            paths: List of absolute file paths

        Returns:
            Path: The deepest common directory path, or None if no common path exists
                  (e.g., different drives on Windows)

        Example:
            >>> paths = [Path('/a/b/c/file1.txt'), Path('/a/b/d/file2.txt')]
            >>> self._find_common_path(paths)
            Path('/a/b')
        """
        if not paths:
            return Path.cwd()

        try:
            # On Windows, check if all paths have the same drive
            drives = {p.drive for p in paths if p.drive}
            if len(drives) > 1:
                # Different drives - cannot compute relative paths
                self._log_warning(
                    "Paths span multiple drives, using absolute paths",
                    {"drives": list(drives)},
                )
                return None

            # Start with the parent of the first file
            common = paths[0].parent

            for path in paths[1:]:
                # Find common prefix between current common and this path's parent
                common_parts = common.parts
                path_parts = path.parent.parts

                # Find the point where the paths diverge
                common = Path(
                    *common_parts[
                        : sum(1 for x, y in zip(common_parts, path_parts, strict=False) if x == y)
                    ]
                )

                # If we've reached the root with no common parts, return None
                if not common.parts:
                    return None

            return common

        except (OSError, ValueError, RuntimeError) as e:
            self._log_warning(
                "Error finding common path, will use absolute paths",
                {"error": str(e), "error_type": type(e).__name__},
            )
            return None

    def _is_safe_relpath(self, rel: str, base_root: Path) -> bool:
        """Validate that a relative path is safe to use.

        This prevents path traversal attacks by checking:
        1. Path is not absolute
        2. Path doesn't contain parent directory references (..)
        3. Resolved path is actually inside base_root

        Args:
            rel: Relative path string to validate
            base_root: The base directory that should contain the file

        Returns:
            True if the path is safe, False otherwise
        """
        try:
            p = Path(rel)
            # Check for absolute paths
            if p.is_absolute():
                return False
            # Check for parent directory references
            if ".." in p.parts:
                return False
            # Check for Windows-style absolute paths (e.g., C:)
            if ":" in rel:
                return False
            # Resolve and verify it stays within base_root
            resolved = (base_root / rel).resolve()
            base_resolved = base_root.resolve()
            # Use string comparison with separator to avoid false positives
            # (e.g., /foo/bar shouldn't match /foo/barbaz)
            base_str = str(base_resolved) + os.sep
            return str(resolved).startswith(base_str) or resolved == base_resolved
        except (TypeError, ValueError, OSError):
            return False

    def _ics_escape(self, text: str) -> str:
        r"""Escape text for iCalendar format per RFC 5545.

        Escapes special characters in iCalendar text values:
        - Backslash (\) → \\
        - Comma (,) → \,
        - Semicolon (;) → \;
        - Newline (\n) → \\n

        Args:
            text: Text to escape

        Returns:
            Escaped text safe for iCalendar format
        """
        return (
            text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")
        )

    def _ics_fold(self, line: str) -> str:
        """Fold long iCalendar lines per RFC 5545 (max 75 octets).

        RFC 5545 requires lines longer than 75 octets to be split across multiple
        lines, with continuation lines starting with a space or tab.

        Args:
            line: Single iCalendar line to fold

        Returns:
            Folded line with CRLF and continuation spaces as needed
        """
        # RFC 5545 specifies 75 octets max per line (bytes, not chars)
        max_octets = 75
        encoded = line.encode("utf-8")

        if len(encoded) <= max_octets:
            return line

        # Split into chunks, ensuring we don't break UTF-8 sequences
        result_lines = []
        current_pos = 0

        while current_pos < len(encoded):
            # Take up to max_octets from current position
            chunk_size = (
                max_octets if current_pos == 0 else max_octets - 1
            )  # -1 for continuation space
            chunk = encoded[current_pos : current_pos + chunk_size]

            # Try to decode; if it fails (broken UTF-8), back up one byte at a time
            while chunk:
                try:
                    decoded_chunk = chunk.decode("utf-8")
                    break
                except UnicodeDecodeError:
                    chunk = chunk[:-1]
            else:
                # Should never happen with valid UTF-8 input, but safety fallback
                break

            if current_pos == 0:
                result_lines.append(decoded_chunk)
            else:
                result_lines.append(" " + decoded_chunk)  # Continuation line starts with space

            current_pos += len(chunk)

        return "\r\n".join(result_lines)

    def export_ics(self, out_dir: Path | str, plan: Plan) -> Path | None:
        """Export compliance deadlines from the plan to an iCalendar (.ics) file.

        This method generates an iCalendar file containing all the compliance deadlines
        specified in the plan's 'due_hints' section. Each deadline is created as an all-day event.

        Args:
            out_dir: Directory where the .ics file will be saved.
                     Will be created if it doesn't exist.
            plan: Dictionary containing the compliance plan with 'due_hints' key.

        Returns:
            Path to the generated .ics file, or None if no valid deadlines were found.

        Raises:
            ValueError: If out_dir is not a directory or cannot be created.
            OSError: If there are issues writing the file.
            KeyError: If required plan structure is missing.

        Example:
            >>> plan = {
            ...     "due_hints": {
            ...         "Control 5": "2024-02-02",
            ...         "Control 6": "2024-03-15"
            ...     }
            ... }
            >>> ics_path = exporter.export_ics("/path/to/output", plan)
            >>> print(f"Exported to: {ics_path}")
            Exported to: /path/to/output/starter-deadlines.ics
        """
        # Input validation
        if not plan:
            self._log_info("Skipping ICS export: Empty plan provided")
            return None

        out_dir = Path(out_dir).resolve()
        due_hints = plan.get("due_hints", {})

        # Early return if no due hints
        if not due_hints:
            self._log_info("Skipping ICS export: No due dates found in plan")
            return None

        self._log_debug(
            "Starting ICS export", {"output_dir": str(out_dir), "due_date_count": len(due_hints)}
        )

        try:
            # Ensure output directory exists
            out_dir.mkdir(parents=True, exist_ok=True)

            # Initialize iCalendar content with header
            lines = [
                "BEGIN:VCALENDAR",
                "VERSION:2.0",
                "PRODID:-//intrinsical-policy-engine//Compliance Calendar//EN",
                "CALSCALE:GREGORIAN",
                "METHOD:PUBLISH",
            ]

            # Add current timestamp in UTC
            now = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
            event_count = 0

            # Generate plan-specific domain for UIDs (use digest of plan for uniqueness)
            plan_hash = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
            uid_domain = f"ctrl-act-{plan_hash}.intrinsical-policy-engine.invalid"

            # Process each deadline
            for _i, (article, due_date_str) in enumerate(due_hints.items(), 1):
                if not due_date_str:
                    self._log_debug("Skipping empty due date", {"article": article})
                    continue

                try:
                    # Parse and validate date
                    dt = datetime.strptime(due_date_str, "%Y-%m-%d").replace(tzinfo=UTC)
                    dtend = dt + timedelta(days=1)

                    # Create iCalendar event with RFC 5545 compliant formatting
                    # UID: use article slug + plan hash + domain for global uniqueness
                    article_slug = article.replace(" ", "-").replace(".", "").lower()
                    event_uid = f"{article_slug}.{plan_hash}@{uid_domain}"

                    # Escape and fold SUMMARY and DESCRIPTION per RFC 5545
                    summary_text = self._ics_escape(f"Compliance - {article} deadline")
                    description_text = self._ics_escape(
                        f"Deadline for {article} compliance under the active framework pack"
                    )

                    event_lines = [
                        "BEGIN:VEVENT",
                        f"DTSTAMP:{now}",
                        f"DTSTART;VALUE=DATE:{dt.strftime('%Y%m%d')}",
                        f"DTEND;VALUE=DATE:{dtend.strftime('%Y%m%d')}",
                        self._ics_fold(f"SUMMARY:{summary_text}"),
                        self._ics_fold(f"DESCRIPTION:{description_text}"),
                        f"UID:{event_uid}",
                        "STATUS:CONFIRMED",
                        "SEQUENCE:0",
                        "END:VEVENT",
                    ]
                    lines.extend(event_lines)
                    event_count += 1

                    self._log_debug(
                        "Added event to calendar",
                        {"article": article, "due_date": due_date_str, "event_uid": event_uid},
                    )

                except ValueError as e:
                    self._log_warning(
                        "Invalid date format in due_hints",
                        {
                            "article": article,
                            "due_date": due_date_str,
                            "error": str(e),
                            "expected_format": "YYYY-MM-DD",
                        },
                    )
                    continue

            # Check if we added any events
            if event_count == 0:
                self._log_info("No valid events to export to ICS")
                return None

            # Close the calendar
            lines.append("END:VCALENDAR")

            # Write to file with proper line endings for iCalendar format
            out_path = out_dir / ICS_FILE
            ics_content = "\r\n".join(lines)

            try:
                out_path.write_text(ics_content, encoding="utf-8")
                self._log_info(
                    "Successfully exported ICS file",
                    {
                        "path": str(out_path),
                        "event_count": event_count,
                        "file_size_bytes": len(ics_content.encode("utf-8")),
                    },
                )
                return out_path

            except OSError as e:
                error_msg = f"Failed to write ICS file: {out_path}"
                self._log_error(error_msg, {"error": str(e), "error_type": type(e).__name__})
                raise OSError(f"{error_msg}: {e}") from e

        except Exception as e:
            self._log_error(
                "Unexpected error during ICS export",
                {
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "out_dir": str(out_dir),
                    "plan_keys": list(plan.keys()),
                },
            )
            raise

    def load_evidence_map(
        self, templates_dir: str | Path
    ) -> dict[str, list[dict[str, Any]]] | None:
        """Load the evidence map from the specified templates directory.

        This method loads a mapping of articles to their associated evidence templates.
        The evidence map is used to determine which template files should be included
        in the exported evidence package for each article.

        Args:
            templates_dir: Path to the directory containing evidence templates.
                          Can be a string or Path object.

        Returns:
            A dictionary mapping article identifiers to lists of evidence template
            configurations, or None if the evidence map could not be loaded.
            Each template configuration is a dictionary with template metadata.

        Raises:
            OSError: If there are issues accessing the templates directory.
            ValueError: If templates_dir is empty or invalid.

        Example:
            >>> evidence_map = exporter.load_evidence_map("/path/to/templates")
            >>> if evidence_map:
            ...     print(f"Loaded {len(evidence_map)} article templates")
        """
        if not templates_dir:
            self._log_error("Cannot load evidence map: templates_dir is empty")
            raise ValueError("templates_dir cannot be empty")

        self._log_debug("Loading evidence map", {"templates_dir": str(templates_dir)})

        try:
            templates_path = Path(templates_dir).resolve()
            if not templates_path.exists():
                self._log_warning(
                    "Templates directory does not exist", {"templates_dir": str(templates_path)}
                )
                return None

            evidence_map = _load_ev_map(str(templates_path))

            if evidence_map:
                self._log_info(
                    "Successfully loaded evidence map",
                    {
                        "article_count": len(evidence_map),
                        "total_templates": sum(
                            len(templates) for templates in evidence_map.values()
                        ),
                    },
                )
            else:
                self._log_warning("Evidence map is empty or could not be loaded")

            return evidence_map

        except Exception as e:
            self._log_error(
                "Failed to load evidence map",
                {
                    "templates_dir": str(templates_dir),
                    "error": str(e),
                    "error_type": type(e).__name__,
                },
            )
            raise

    def selected_articles_from_plan(self, plan: dict[str, Any] | Plan) -> set[str]:
        """Extract a set of selected article identifiers from a compliance plan.

        This method extracts article identifiers from either the 'articles_overlay'
        or 'actions_meta' sections of the plan, with 'articles_overlay' taking
        precedence if present.

        Args:
            plan: Dictionary containing the compliance plan with either:
                  - articles_overlay: Dict mapping article IDs to action IDs
                  - actions_meta: List of action metadata dicts with 'articles' keys

        Returns:
            A set of article identifiers (as strings) that are referenced in the plan.
            Returns an empty set if no articles are found.

        Raises:
            TypeError: If the plan is not a dictionary.
            ValueError: If the plan is empty or malformed.

        Example:
            >>> plan = {
            ...     "articles_overlay": {"TOPIC-1": ["act1"], "TOPIC-2": ["act2"]},
            ...     "actions_meta": [{"articles": ["TOPIC-3"]}]
            ... }
            >>> articles = exporter.selected_articles_from_plan(plan)
            >>> sorted(articles)
            ['TOPIC-1', 'TOPIC-2']
        """
        if not isinstance(plan, dict):
            error_msg = f"Expected plan to be a dictionary, got {type(plan).__name__}"
            self._log_error(error_msg)
            raise TypeError(error_msg)

        if not plan:
            self._log_warning("Empty plan provided to selected_articles_from_plan")
            return set()

        # First check for articles in articles_overlay
        overlay = plan.get("articles_overlay", {}) or {}
        if overlay:
            articles = set(overlay.keys())
            self._log_debug(
                "Selected articles from overlay",
                {"article_count": len(articles), "articles": sorted(articles)},
            )
            return articles

        # Fall back to extracting from actions_meta if no overlay
        extracted_articles: set[str] = set()
        actions_meta = plan.get("actions_meta", [])

        if not isinstance(actions_meta, (list, tuple)):
            error_msg = f"Expected actions_meta to be a list, got {type(actions_meta).__name__}"
            self._log_error(error_msg)
            raise TypeError(error_msg)

        for action in actions_meta:
            if not isinstance(action, dict):
                self._log_warning("Skipping invalid action in actions_meta (not a dictionary)")
                continue

            action_articles = action.get("articles", [])
            if not isinstance(action_articles, (list, tuple)):
                self._log_warning(
                    "Skipping action with non-list articles field",
                    {
                        "action_id": action.get("id", "unknown"),
                        "articles_type": type(action_articles).__name__,
                    },
                )
                continue

            for article in action_articles:
                try:
                    extracted_articles.add(str(article))
                except (TypeError, ValueError) as e:
                    self._log_warning(
                        "Failed to convert article to string", {"article": article, "error": str(e)}
                    )

        self._log_debug(
            "Extracted articles from actions_meta",
            {"article_count": len(extracted_articles), "articles": sorted(extracted_articles)},
        )

        return extracted_articles

    def build_evidence_manifest(  # noqa: C901
        self,
        templates_dir: str | Path,
        selected_articles: set[str],
        article_map: dict[str, list[dict[str, Any]]] | None = None,
    ) -> tuple[EvidenceManifest, set[Path], Path]:
        """Build a manifest of evidence files based on selected articles and templates.

        This method creates a manifest that maps selected articles to their corresponding
        evidence template files. It handles both directory and file-based templates,
        and tracks any missing or invalid template references.

        Args:
            templates_dir: Directory containing evidence templates. Can be a string or Path.
            selected_articles: Set of article identifiers to include in the manifest.
            article_map: Optional mapping of article IDs to template configurations.
                        If None, all files in the templates directory will be included.

        Returns:
            A tuple containing:
            - manifest: Dictionary with the following structure:
                - root: Base directory for templates
                - selected_articles: Sorted list of selected article IDs
                - by_article: Mapping of article IDs to their template configurations
                - included: Sorted list of included file paths (relative to root)
                - missing: List of missing or invalid template references
            - wanted: Set of absolute Path objects to all included files
            - base: Path to the evidence templates directory

        Raises:
            ValueError: If templates_dir is empty or invalid.
            OSError: If there are issues accessing the filesystem.
            TypeError: If inputs are of incorrect types.

        Example:
            >>> templates = "/path/to/templates"
            >>> articles = {"TOPIC-1": ["template1.md", "dir1/"]}
            >>> manifest, files, base = exporter.build_evidence_manifest(
            ...     templates_dir=templates,
            ...     selected_articles={"TOPIC-1"},
            ...     article_map={"TOPIC-1": [{"path": "template1.md"}, {"path": "dir1/"}]}
            ... )
            >>> print(f"Included {len(manifest['included'])} files")
        """
        if not templates_dir:
            error_msg = "Cannot build evidence manifest: templates_dir is required"
            self._log_error(error_msg)
            raise ValueError(error_msg)

        self._log_debug(
            "Building evidence manifest",
            {
                "templates_dir": str(templates_dir),
                "selected_article_count": len(selected_articles),
                "has_article_map": article_map is not None,
            },
        )

        try:
            layout = load_framework_layout_from_path(Path(templates_dir))
            framework_dir = layout.framework_dir
            base = layout.evidence_templates_dir
            root_rel = f"./{base.relative_to(framework_dir).as_posix()}"

            if not base.exists():
                self._log_warning(
                    "Evidence templates directory not found",
                    {"path": str(base), "selected_articles": sorted(selected_articles)},
                )

                return (
                    {
                        "root": root_rel,
                        "selected_articles": sorted(selected_articles),
                        "by_article": {},
                        "included": [],
                        "missing": [],
                    },
                    set(),
                    base,
                )

            wanted: set[Path] = set()
            by_article: dict[str, list[str]] = {}
            missing: list[dict[str, str]] = []

            if article_map is None:
                # If no article map provided, include all files in the templates directory
                self._log_debug(
                    "No article map provided, including all files in templates directory"
                )
                for p in base.rglob("*"):
                    if p.is_file() and not p.name.startswith("."):  # Skip hidden files
                        wanted.add(p)
            else:
                # Process each selected article and its template configurations
                for art in sorted(selected_articles):
                    entries = article_map.get(art, []) or []
                    paths_for_article: list[str] = []

                    if not entries:
                        self._log_debug(f"No templates found for article {art}")
                        by_article[art] = paths_for_article
                        continue

                    self._log_debug(f"Processing {len(entries)} templates for article {art}")

                    for entry in entries:
                        try:
                            # Normalize entry to path string
                            pstr = (
                                str(entry.get("path", ""))
                                if isinstance(entry, dict)
                                else str(entry)
                            )
                            if not pstr:
                                self._log_warning(
                                    "Empty path in template entry",
                                    {"article": art, "entry": str(entry)},
                                )
                                missing.append({"article": art, "path": "", "reason": "empty_path"})
                                continue

                            src = (base / pstr).resolve()

                            # Track the template path for manifest purposes
                            paths_for_article.append(pstr)

                            # Check for directory templates (ending with '/')
                            if pstr.endswith("/"):
                                dir_path = (base / pstr.rstrip("/")).resolve()

                                # Ensure we don't traverse outside the base directory
                                try:
                                    dir_path.relative_to(base)
                                except ValueError:
                                    self._log_warning(
                                        "Directory traversal attempt detected",
                                        {"article": art, "path": pstr, "resolved": str(dir_path)},
                                    )
                                    missing.append(
                                        {"article": art, "path": pstr, "reason": "invalid_path"}
                                    )
                                    continue

                                if dir_path.exists() and dir_path.is_dir():
                                    # Recursively add all files in the directory
                                    file_count = 0
                                    for p in dir_path.rglob("*"):
                                        if p.is_file() and not p.name.startswith("."):
                                            wanted.add(p)
                                            file_count += 1
                                    self._log_debug(
                                        f"Added {file_count} files from directory template",
                                        {
                                            "article": art,
                                            "template": pstr,
                                            "directory": str(dir_path),
                                        },
                                    )
                                else:
                                    self._log_warning(
                                        "Template directory not found",
                                        {
                                            "article": art,
                                            "template": pstr,
                                            "resolved": str(dir_path),
                                        },
                                    )
                                    missing.append(
                                        {"article": art, "path": pstr, "reason": "dir_not_found"}
                                    )
                            else:
                                # Handle single file template
                                try:
                                    src.relative_to(base)  # Security check
                                    if src.exists() and src.is_file():
                                        wanted.add(src)
                                        self._log_debug(
                                            "Added file template",
                                            {
                                                "article": art,
                                                "template": pstr,
                                                "resolved": str(src),
                                            },
                                        )
                                    else:
                                        self._log_warning(
                                            "Template file not found",
                                            {
                                                "article": art,
                                                "template": pstr,
                                                "resolved": str(src),
                                            },
                                        )
                                        missing.append(
                                            {
                                                "article": art,
                                                "path": pstr,
                                                "reason": "file_not_found",
                                            }
                                        )
                                except ValueError:
                                    self._log_warning(
                                        "Invalid template path (outside base directory)",
                                        {"article": art, "template": pstr, "resolved": str(src)},
                                    )
                                    missing.append(
                                        {"article": art, "path": pstr, "reason": "invalid_path"}
                                    )

                        except (OSError, ValueError, TypeError, RuntimeError) as e:
                            self._log_error(
                                f"Error processing template entry for article {art}",
                                {
                                    "entry": str(entry),
                                    "error": str(e),
                                    "error_type": type(e).__name__,
                                },
                            )
                            missing.append(
                                {
                                    "article": art,
                                    "path": str(entry)[:100],  # Truncate long paths
                                    "reason": f"processing_error: {type(e).__name__}",
                                }
                            )

                    # Record normalized template paths for this article in the manifest
                    by_article[art] = paths_for_article

            # Prepare the final manifest
            included_paths = set()
            for fp in wanted:
                try:
                    rel_path = fp.relative_to(base).as_posix()
                    included_paths.add(rel_path)
                except ValueError:
                    self._log_warning(
                        "File not relative to base directory", {"file": str(fp), "base": str(base)}
                    )

            manifest: EvidenceManifest = {
                "root": root_rel,
                "selected_articles": sorted(selected_articles),
                "by_article": by_article,
                "included": sorted(included_paths),
                "missing": missing,
            }

            self._log_info(
                "Evidence manifest created",
                {
                    "included_files": len(manifest["included"]),
                    "missing_entries": len(manifest["missing"]),
                    "selected_articles": len(manifest["selected_articles"]),
                    "articles_with_templates": len(manifest["by_article"]),
                },
            )

            return manifest, wanted, base

        except Exception as e:
            self._log_error(
                "Failed to build evidence manifest",
                {
                    "templates_dir": str(templates_dir),
                    "error": str(e),
                    "error_type": type(e).__name__,
                    "selected_article_count": len(selected_articles),
                },
            )
            raise

    def zip_selected_evidence(
        self,
        templates_dir: str,
        out_dir: Path,
        selected_articles: set[str],
        article_map: dict[str, list[dict[str, Any]]] | None,
    ) -> tuple[Path | None, dict[str, Any]]:
        """Bundle evidence files referenced by selected articles into a zip archive."""
        logger: StructuredLogger | None = getattr(self, "_logger", None)
        manifest, wanted, base = self.build_evidence_manifest(
            templates_dir,
            selected_articles,
            article_map,
        )
        if not base.exists():
            if logger:
                logger.warning(
                    "export.evidence.skipped",
                    {"reason": "templates_not_found", "path": str(base)},
                )
            return None, cast(dict[str, Any], manifest)
        if not wanted:
            if logger:
                logger.warning(
                    "export.evidence.empty",
                    {
                        "missing_count": len(manifest.get("missing", [])),
                        "articles": len(selected_articles),
                    },
                )
            return None, cast(dict[str, Any], manifest)

        zpath = out_dir / EVIDENCE_ZIP
        zpath.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as z:
            for fp in sorted(wanted, key=lambda p: p.as_posix()):
                arcname = fp.relative_to(base).as_posix()
                z.write(fp, arcname=arcname)
        if logger:
            logger.info(
                "export.evidence.created",
                {
                    "zip": str(zpath),
                    "files_included": len(wanted),
                    "articles": len(selected_articles),
                    "missing": len(manifest.get("missing", [])),
                },
            )
        # O-04: evidence_manifest.json goes to _metadata/ (plumbing file)
        from intrinsical_policy_engine.app.config.constants import METADATA_DIR

        metadata_dir = out_dir / METADATA_DIR
        metadata_dir.mkdir(parents=True, exist_ok=True)
        man_path = metadata_dir / EVIDENCE_MANIFEST
        if not man_path.exists():
            portable_manifest = self.serializable_evidence_manifest(manifest)
            self.write_text(
                man_path,
                json.dumps(portable_manifest, indent=2, ensure_ascii=False),
            )
        return zpath, cast(dict[str, Any], manifest)

    def zip_from_manifest(
        self,
        out_dir: Path,
        manifest: dict[str, Any],
        *,
        base_root: Path,
    ) -> Path | None:
        """Recreate an evidence zip using an explicit, validated pack root.

        The serialized manifest deliberately contains only a portable relative
        ``root``. Resolving that value against the process CWD is ambiguous and
        makes output host-dependent, so callers must supply the layout-owned
        evidence root used to build the manifest.
        """
        if not isinstance(manifest, dict):
            return None
        try:
            base_root = base_root.resolve(strict=True)
        except (TypeError, ValueError, OSError):
            return None
        if not base_root.exists() or not base_root.is_dir():
            return None
        raw_included = manifest.get("included") or []
        try:
            if isinstance(raw_included, (list, tuple, set)):
                included: list[str] = []
                for rel in raw_included:
                    if isinstance(rel, (str, os.PathLike)):
                        included.append(os.fspath(rel))
                    else:
                        return None
            else:
                return None
        except (TypeError, ValueError):
            return None
        zpath = out_dir / EVIDENCE_ZIP
        zpath.parent.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(zpath, "w", compression=zipfile.ZIP_DEFLATED) as zf:
                for rel in sorted(included):
                    # P0.3 Fix: Path traversal protection
                    if not self._is_safe_relpath(rel, base_root):
                        self._log_warning(
                            "Skipping unsafe path in manifest",
                            {"relpath": rel, "base_root": str(base_root)},
                        )
                        continue
                    src = (base_root / rel).resolve(strict=False)
                    if src.exists() and src.is_file():
                        zf.write(src, arcname=rel)
        except OSError:
            return None
        # O-04: evidence_manifest.json goes to _metadata/ (plumbing file)
        from intrinsical_policy_engine.app.config.constants import METADATA_DIR

        metadata_dir = out_dir / METADATA_DIR
        metadata_dir.mkdir(parents=True, exist_ok=True)
        man_path = metadata_dir / EVIDENCE_MANIFEST
        if not man_path.exists():
            with contextlib.suppress(OSError):
                portable_manifest = self.serializable_evidence_manifest(manifest)
                self.write_text(
                    man_path,
                    json.dumps(portable_manifest, indent=2, ensure_ascii=False),
                )
        return zpath

    # evidence_summary provided by ExportHelpers
