# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Evidence quality from actual bytes; front matter never grants readiness."""

from __future__ import annotations

import csv
import io
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

import yaml

from intrinsical_policy_engine.common.document_io import parse_document
from intrinsical_policy_engine.common.text.front_matter import strip_front_matter

Quality = Literal["ready", "draft", "placeholder"]
CSV_FIELD_LIMIT = 131_072


@dataclass(frozen=True)
class QualityConfig:
    md_draft_min_chars: int = 150
    md_ready_min_chars: int = 400
    min_words: int = 50
    json_min_keys: int = 3
    yaml_min_keys: int = 3
    csv_min_cols: int = 2
    csv_min_data_rows: int = 2
    csv_ready_min_data_rows: int = 5
    placeholder_markers: tuple[str, ...] = ("TODO", "LOREM", "[FILL:", "[REQUIRED:")


class QualityEngine:
    def __init__(self, cfg: QualityConfig | None = None) -> None:
        self.cfg = cfg or QualityConfig()
        self._placeholder_patterns = tuple(
            re.compile(
                rf"\b{re.escape(marker)}\b" if marker.isidentifier() else re.escape(marker),
                re.IGNORECASE,
            )
            for marker in self.cfg.placeholder_markers
        )

    def _has_placeholder(self, value: object) -> bool:
        pending = [value]
        seen: set[int] = set()
        while pending:
            item = pending.pop()
            if isinstance(item, str):
                if any(pattern.search(item) is not None for pattern in self._placeholder_patterns):
                    return True
            elif isinstance(item, (dict, list, tuple, set, frozenset)) and id(item) not in seen:
                seen.add(id(item))
                pending.extend(item.values() if isinstance(item, dict) else item)
        return False

    @staticmethod
    def _has_content(value: object) -> bool:
        pending = [value]
        seen: set[int] = set()
        while pending:
            item = pending.pop()
            if isinstance(item, (dict, list, tuple, set, frozenset)):
                if id(item) not in seen:
                    seen.add(id(item))
                    pending.extend(item.values() if isinstance(item, dict) else item)
            elif item is not None and (not isinstance(item, (str, bytes)) or bool(item.strip())):
                return True
        return False

    def classify_bytes(self, data: bytes, suffix: str) -> Quality:
        if not data:
            return "placeholder"
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            return "placeholder"
        suffix = suffix.lower()
        if suffix == ".md":
            return self._classify_markdown(strip_front_matter(text))
        if suffix in {".json", ".yaml", ".yml"}:
            return self._classify_document(text, suffix)
        if suffix == ".csv":
            return self._classify_csv(text)
        return "placeholder"

    def _classify_markdown(self, text: str) -> Quality:
        if self._has_placeholder(text):
            return "placeholder"
        structured = any(line.startswith("## ") for line in text.splitlines())
        if (
            len(text.strip()) >= self.cfg.md_ready_min_chars
            and len(text.split()) >= self.cfg.min_words
            and structured
        ):
            return "ready"
        return "draft" if len(text.strip()) >= self.cfg.md_draft_min_chars else "placeholder"

    def _classify_document(self, text: str, suffix: str) -> Quality:
        try:
            obj = parse_document(text, suffix)
        except (ValueError, yaml.YAMLError):
            return "placeholder"
        if self._has_placeholder(obj):
            return "placeholder"
        required = self.cfg.json_min_keys if suffix == ".json" else self.cfg.yaml_min_keys
        if (
            isinstance(obj, dict)
            and sum(self._has_content(value) for value in obj.values()) >= required
        ):
            return "ready"
        return "draft"

    def _classify_csv(self, text: str) -> Quality:
        try:
            rows = list(csv.reader(io.StringIO(text, newline=""), strict=True))
        except csv.Error as exc:
            raise ValueError(
                f"Invalid CSV evidence (field limit {CSV_FIELD_LIMIT} characters): {exc}"
            ) from exc
        if any(len(cell) > CSV_FIELD_LIMIT for row in rows for cell in row):
            raise ValueError(f"CSV evidence cell exceeds limit {CSV_FIELD_LIMIT} characters")
        if any(self._has_placeholder(cell) for row in rows for cell in row):
            return "placeholder"
        if not rows:
            return "placeholder"
        header, *rows = rows
        if len(header) < self.cfg.csv_min_cols or any(not cell.strip() for cell in header):
            return "placeholder"
        data_rows = [row for row in rows if any(cell.strip() for cell in row)]
        if len(data_rows) < self.cfg.csv_min_data_rows or any(
            len(row) != len(header) for row in data_rows
        ):
            return "placeholder"
        return "ready" if len(data_rows) >= self.cfg.csv_ready_min_data_rows else "draft"

    def classify_file(self, path: Path) -> Quality:
        return self.classify_bytes(path.read_bytes(), path.suffix)

    def diagnose_file(self, path: Path) -> tuple[bool, str]:
        if not path.is_file():
            return False, "absent"
        quality = self.classify_file(path)
        return quality == "ready", "ok" if quality == "ready" else "quality_fail"
