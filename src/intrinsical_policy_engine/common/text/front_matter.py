# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Strip Markdown front matter before classifying actual evidence content."""

from __future__ import annotations

BOM = "\ufeff"


def _strip_bom(text: str) -> str:
    """Remove a UTF-8 BOM while preserving other leading whitespace."""
    return text[1:] if text.startswith(BOM) else text


def strip_front_matter(text: str) -> str:
    """Return the body after leading front matter, preserving its line endings.

    A leading delimiter opens metadata even when its closing delimiter is
    missing. Such an unfinished block has no classifiable Markdown body.
    """
    cleaned = _strip_bom(text)
    lines = cleaned.splitlines(keepends=True)
    opening = next((index for index, line in enumerate(lines) if line.strip()), None)
    if opening is None or lines[opening].strip() != "---":
        return cleaned
    for closing in range(opening + 1, len(lines)):
        if lines[closing].strip() == "---":
            return "".join(lines[closing + 1 :])
    return ""
