# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Front-matter helpers reused by CLI/UI."""

from __future__ import annotations

import yaml

BOM = "\ufeff"


def _strip_bom(text: str) -> str:
    """Remove a UTF-8 BOM if present.

    Markdown files occasionally include a BOM at the start of the file, which
    would prevent the front matter markers from being detected. We strip only
    the BOM while leaving any other leading whitespace intact.
    """

    return text[1:] if text.startswith(BOM) else text


def _looks_like_front_matter_block(block: str) -> bool:
    """Heuristic guard to avoid treating markdown as YAML front-matter.

    We keep this intentionally lightweight: if the candidate block contains
    lines that clearly look like markdown content (headers, quotes, code
    fences), we assume it is not a pure YAML header and skip parsing.
    """

    for line in block.split("\n"):
        stripped = line.strip()
        if not stripped:
            continue
        # Obvious markdown constructs that should not appear in YAML front-matter
        if stripped.startswith(("#", ">", "`")):
            return False
    return True


def strip_front_matter(text: str) -> str:
    """Remove YAML front-matter section from Markdown text."""
    cleaned = _strip_bom(text)
    t = cleaned.replace("\r\n", "\n")
    if not t.lstrip().startswith("---\n"):
        return cleaned
    end = t.find("\n---\n", 4)
    if end == -1:
        return cleaned
    return t[end + 5 :]


def parse_front_matter(text: str) -> dict | None:
    """Parse YAML front-matter and return it as a dict if present."""
    text = _strip_bom(text)
    if not text.lstrip().startswith("---"):
        return None
    t = text.replace("\r\n", "\n")
    if not t.startswith("---\n"):
        return None
    end = t.find("\n---\n", 4)
    if end == -1:
        return None
    fm_raw = t[4:end]
    # Heuristic: if the candidate block already looks like markdown content,
    # do not attempt to parse it as YAML front-matter.
    if not _looks_like_front_matter_block(fm_raw):
        return None
    try:
        data = yaml.safe_load(fm_raw) or {}
        return data if isinstance(data, dict) else None
    except (yaml.YAMLError, ValueError):
        return None
