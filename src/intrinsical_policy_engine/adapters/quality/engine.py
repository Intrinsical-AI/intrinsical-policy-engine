# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Evidence quality heuristics to classify files and directories."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, cast

import yaml

from intrinsical_policy_engine.common.constants import DEFAULT_ENCODING
from intrinsical_policy_engine.common.io_utils import read_text_safe
from intrinsical_policy_engine.common.text.front_matter import strip_front_matter
from intrinsical_policy_engine.domain.constants import MD_DRAFT_MIN_CHARS, MD_READY_MIN_CHARS

Quality = Literal["ready", "draft", "placeholder"]
Reason = Literal[
    "ok",
    "absent",
    "binary_empty",
    "md_too_short",
    "md_placeholder",
    "md_read_error",
    "json_parse_error",
    "json_insufficient_keys",
    "yaml_parse_error",
    "yaml_insufficient_keys",
    "csv_too_short",
    "csv_invalid_header",
    "dir_requirement_missing",
    "parse_error",
    "unsupported_extension",
]


@dataclass(frozen=True)
class QualityConfig:
    """Configuration knobs controlling file/heuristic thresholds."""

    md_draft_min_chars: int = MD_DRAFT_MIN_CHARS
    md_ready_min_chars: int = MD_READY_MIN_CHARS
    json_min_keys: int = 3
    yaml_min_keys: int = 3
    csv_min_cols: int = 2
    csv_min_rows: int = 2  # including header
    csv_ready_min_rows: int = 5
    placeholder_markers: tuple[str, ...] = ("TODO", "LOREM", "[FILL:")


class QualityEngine:
    """Classify evidence files and compute readiness heuristics."""

    def __init__(self, cfg: QualityConfig | None = None):
        """Initialize heuristics and dynamically load optional quality gates."""
        self.cfg = cfg or QualityConfig()
        # Quality gates registry for file-specific validation
        self._gates: dict = {}
        try:
            from intrinsical_policy_engine.adapters.quality.gates import (
                HITLQualityGate,
                ImpactReviewQualityGate,
            )

            self._gates = {
                "hitl-policy.v1.md": HITLQualityGate(),
                "dep.doc.a27.impact_review-template.v1.md": ImpactReviewQualityGate(),
            }
        except ImportError:
            # Quality gates not available, fall back to heuristics
            pass

    # --- helpers ---
    def _strip_front_matter(self, text: str) -> str:
        """Strip YAML front-matter in Markdown evidence."""
        return strip_front_matter(text)

    # --- Per-extension diagnostic handlers ---
    def _diagnose_json(self, path: Path) -> tuple[bool, Reason]:
        """Diagnose JSON file validity."""
        try:
            data = json.loads(read_text_safe(str(path)) or "")
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return False, "json_parse_error"
        if path.name == "evidence_quality.json":
            return self._diagnose_evidence_quality_data(data)
        if isinstance(data, dict):
            nz = [k for k, v in data.items() if v not in (None, "", [], {})]
            ok = len(nz) >= self.cfg.json_min_keys
            return ok, ("ok" if ok else "json_insufficient_keys")
        return False, "json_insufficient_keys"

    @staticmethod
    def _diagnose_evidence_quality_data(data: object) -> tuple[bool, Reason]:
        """Validate the generated evidence-quality report's stable shape.

        Empty maps are meaningful for a starter pack with no missing evidence,
        so this artifact cannot use the generic non-empty-key heuristic.
        """
        if not isinstance(data, dict):
            return False, "json_insufficient_keys"
        required_maps = ("quality_by_file", "missing_reasons_by_article")
        if not all(key in data for key in required_maps):
            return False, "json_insufficient_keys"
        if not all(isinstance(data[key], dict) for key in required_maps):
            return False, "json_insufficient_keys"
        return True, "ok"

    def _diagnose_yaml(self, path: Path) -> tuple[bool, Reason]:
        """Diagnose YAML file validity."""
        try:
            data = yaml.safe_load(read_text_safe(str(path)) or "")
        except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError):
            return False, "yaml_parse_error"
        if isinstance(data, dict):
            ok = len([1 for k, v in data.items() if v]) >= self.cfg.yaml_min_keys
            return ok, ("ok" if ok else "yaml_insufficient_keys")
        return False, "yaml_insufficient_keys"

    def _diagnose_md(self, path: Path) -> tuple[bool, Reason]:
        """Diagnose Markdown file validity."""
        try:
            text = path.read_text(encoding=DEFAULT_ENCODING, errors="ignore")
            text = self._strip_front_matter(text)
        except (OSError, UnicodeDecodeError):
            return False, "md_read_error"
        up = text.upper()
        if len(text.strip()) < self.cfg.md_draft_min_chars:
            return False, "md_too_short"
        if any(m in up for m in self.cfg.placeholder_markers):
            return False, "md_placeholder"
        return True, "ok"

    def _diagnose_csv(self, path: Path) -> tuple[bool, Reason]:
        """Diagnose CSV file validity."""
        try:
            lines = path.read_text(encoding=DEFAULT_ENCODING, errors="ignore").splitlines()
        except (OSError, UnicodeDecodeError):
            return False, "csv_too_short"
        data_lines = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
        if len(data_lines) < self.cfg.csv_min_rows:
            return False, "csv_too_short"
        header = data_lines[0]
        sep = (
            "," if "," in header else (";" if ";" in header else ("\t" if "\t" in header else ","))
        )
        cols = len(header.split(sep))
        return (
            cols >= self.cfg.csv_min_cols,
            "ok" if cols >= self.cfg.csv_min_cols else "csv_invalid_header",
        )

    def _diagnose_xlsx(self, path: Path) -> tuple[bool, Reason]:
        """Diagnose XLSX file validity (lightweight ZIP signature check)."""
        try:
            with path.open("rb") as f:
                sig = f.read(4)
            if not sig:
                return False, "binary_empty"
            if not sig.startswith(b"PK"):
                return False, "parse_error"
            return True, "ok"
        except (OSError, UnicodeDecodeError, ValueError):
            return False, "binary_empty"

    def _diagnose_pdf(self, path: Path) -> tuple[bool, Reason]:
        """Diagnose PDF file validity (lightweight %PDF header check)."""
        try:
            with path.open("rb") as f:
                sig = f.read(4)
            if not sig:
                return False, "binary_empty"
            if not sig.startswith(b"%PDF"):
                return False, "parse_error"
            return True, "ok"
        except (OSError, UnicodeDecodeError, ValueError):
            return False, "binary_empty"

    # Extension -> handler method name dispatch
    _DIAGNOSE_HANDLERS: dict[str, str] = {
        ".json": "_diagnose_json",
        ".yaml": "_diagnose_yaml",
        ".yml": "_diagnose_yaml",
        ".md": "_diagnose_md",
        ".csv": "_diagnose_csv",
        ".xlsx": "_diagnose_xlsx",
        ".pdf": "_diagnose_pdf",
    }

    # --- API ---
    def is_valid_file(self, path: Path) -> bool:
        """Return True if the file is considered valid evidence, False otherwise.

        Delegates to diagnose_file() to keep a single source of truth for
        per-extension semantics.
        """
        ok, _ = self.diagnose_file(path)
        return ok

    def diagnose_file(self, path: Path) -> tuple[bool, Reason]:
        """Return (is_valid, reason) for an evidence candidate."""
        try:
            if not path.exists() or not path.is_file():
                return False, "absent"
            if path.stat().st_size <= 0:
                return False, "binary_empty"
            suf = path.suffix.lower()
            handler_name = self._DIAGNOSE_HANDLERS.get(suf)
            if handler_name:
                handler = getattr(self, handler_name)
                return cast(tuple[bool, Reason], handler(path))
            return False, "unsupported_extension"
        except (OSError, UnicodeDecodeError, ValueError):
            return False, "absent"

    # --- Per-extension classification handlers ---
    def _classify_md(self, path: Path) -> Quality:
        """Classify Markdown file quality."""
        text = path.read_text(encoding=DEFAULT_ENCODING, errors="ignore")
        text = self._strip_front_matter(text)
        up = text.upper()
        if len(text.strip()) >= self.cfg.md_ready_min_chars and all(
            m not in up for m in self.cfg.placeholder_markers
        ):
            return "ready"
        if len(text.strip()) >= self.cfg.md_draft_min_chars and all(
            m not in up for m in self.cfg.placeholder_markers
        ):
            return "draft"
        return "placeholder"

    def _classify_json(self, path: Path) -> Quality:
        """Classify JSON file quality."""
        try:
            data = json.loads(read_text_safe(str(path)) or "")
            if isinstance(data, dict):
                nz = [k for k, v in data.items() if v not in (None, "", [], {})]
                return "ready" if len(nz) >= self.cfg.json_min_keys else "draft"
            return "draft"
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
            return "placeholder"

    def _classify_yaml(self, path: Path) -> Quality:
        """Classify YAML file quality."""
        try:
            data = yaml.safe_load(read_text_safe(str(path)) or "")
            if (
                isinstance(data, dict)
                and len([1 for k, v in data.items() if v]) >= self.cfg.yaml_min_keys
            ):
                return "ready"
            return "draft"
        except (OSError, UnicodeDecodeError, yaml.YAMLError, ValueError):
            return "placeholder"

    def _classify_csv(self, path: Path) -> Quality:
        """Classify CSV file quality."""
        lines = path.read_text(encoding=DEFAULT_ENCODING, errors="ignore").splitlines()
        data_lines = [ln for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
        if len(data_lines) >= self.cfg.csv_min_rows:
            header = data_lines[0]
            sep = (
                ","
                if "," in header
                else (";" if ";" in header else ("\t" if "\t" in header else ","))
            )
            cols = len(header.split(sep))
            if cols >= self.cfg.csv_min_cols:
                return "draft" if len(data_lines) < self.cfg.csv_ready_min_rows else "ready"
        return "placeholder"

    def _classify_binary(self, path: Path) -> Quality:
        """Classify binary file (XLSX, PDF) quality."""
        return "draft"

    # Extension -> handler method name dispatch for classification
    _CLASSIFY_HANDLERS: dict[str, str] = {
        ".md": "_classify_md",
        ".json": "_classify_json",
        ".yaml": "_classify_yaml",
        ".yml": "_classify_yaml",
        ".csv": "_classify_csv",
        ".xlsx": "_classify_binary",
        ".pdf": "_classify_binary",
    }

    def classify_file(self, path: Path) -> Quality:
        """Classify a file as ready/draft/placeholder using heuristics."""
        # Check quality gates first (if registered for this filename)
        gate = self._gates.get(path.name)
        if gate:
            is_ready, _ = gate.check(path)
            return "ready" if is_ready else "draft"

        # Fall back to extension-based heuristics
        try:
            if not path.exists() or not path.is_file() or path.stat().st_size <= 0:
                return "placeholder"
            suf = path.suffix.lower()
            handler_name = self._CLASSIFY_HANDLERS.get(suf)
            if handler_name:
                handler = getattr(self, handler_name)
                return cast(Quality, handler(path))
            return "draft"
        except (OSError, UnicodeDecodeError, ValueError):
            return "placeholder"

    def dir_requirement_met(self, base_root: Path, included_set: set[str], rel_dir: str) -> bool:
        """Determine whether a directory requirement (README/manifest) is satisfied."""
        readme_rel = f"{rel_dir}README.md"
        manifest_rel = f"{rel_dir}manifest.json"
        if readme_rel in included_set:
            fp = base_root / readme_rel
            try:
                text = fp.read_text(encoding=DEFAULT_ENCODING, errors="ignore")
                text = self._strip_front_matter(text)
                up = text.upper()
                if len(text.strip()) >= self.cfg.md_draft_min_chars and all(
                    m not in up for m in self.cfg.placeholder_markers
                ):
                    return True
            except (OSError, UnicodeDecodeError):
                return False
        if manifest_rel in included_set:
            fp = base_root / manifest_rel
            try:
                data = json.loads(fp.read_text(encoding=DEFAULT_ENCODING))
                if isinstance(data, dict) and all(
                    k in data and data[k] for k in ("name", "date", "scope")
                ):
                    return True
            except (OSError, UnicodeDecodeError, json.JSONDecodeError, ValueError):
                return False
        return False
