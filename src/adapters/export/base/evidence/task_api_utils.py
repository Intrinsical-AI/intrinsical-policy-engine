# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Helpers for generating descriptive API payload context."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from src.app.config.constants import DEFAULT_ENCODING, EVIDENCE_MANIFEST, MANIFEST_MD, METADATA_DIR


def redact_headers(headers: dict | None) -> dict:
    """Return a copy of headers with sensitive values replaced."""
    if not headers:
        return {}
    sens_keys = {
        "authorization",
        "x-authorization",
        "proxy-authorization",
        "x-api-key",
        "api-key",
        "token",
        "cookie",
        "set-cookie",
    }
    out: dict[str, Any] = {}
    for k, v in headers.items():
        if isinstance(k, str) and k.lower() in sens_keys:
            out[k] = "<redacted>"
        else:
            out[k] = v
    return out


def read_manifest_lines(out_dir: str) -> list[str]:
    """Load manifest markdown or fall back to an auto-generated summary."""
    # Prefer explicit manifest.md if present
    md_path = Path(out_dir) / MANIFEST_MD
    if md_path.exists():
        try:
            content = md_path.read_text(encoding=DEFAULT_ENCODING)
            # Return lines without trailing empty splits; prepend a blank line to separate blocks
            return [""] + [ln for ln in content.splitlines()]
        except (OSError, UnicodeDecodeError):
            # Fall through to evidence manifest summary
            pass
    man_path = Path(out_dir) / METADATA_DIR / EVIDENCE_MANIFEST
    if not man_path.exists():
        man_path = Path(out_dir) / EVIDENCE_MANIFEST
    if not man_path.exists():
        return []
    try:
        man = json.loads(man_path.read_text(encoding=DEFAULT_ENCODING))
        inc = man.get("included") or []
        mis = man.get("missing") or []
        lines: list[str] = [
            "",
            "## Evidence manifest",
            f"- included: {len(inc)}",
            f"- missing: {len(mis)}",
        ]
        if inc:
            lines.append("- included_examples:")
            for s in inc[:5]:
                lines.append(f"  - {s}")
        if mis:
            lines.append("- missing_examples:")
            for m in mis[:5]:
                if isinstance(m, dict):
                    lines.append(f"  - {m.get('path')}")
                else:
                    lines.append(f"  - {m}")
        return lines
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return []


def read_quality_lines(out_dir: str) -> list[str]:
    """Summarize evidence quality stats for textual exports."""
    p = Path(out_dir) / METADATA_DIR / "evidence_quality.json"
    if not p.exists():
        p = Path(out_dir) / "evidence_quality.json"
    if not p.exists():
        return []
    try:
        data = json.loads(p.read_text(encoding=DEFAULT_ENCODING)) or {}
        qmap = data.get("quality_by_file") or {}
        ready = sum(1 for v in qmap.values() if v == "ready")
        draft = sum(1 for v in qmap.values() if v == "draft")
        placeholder = sum(1 for v in qmap.values() if v == "placeholder")
        lines = [
            "",
            "## Evidence quality",
            f"- ready: {ready}",
            f"- draft: {draft}",
            f"- placeholder: {placeholder}",
        ]
        # Show up to 5 missing examples with reasons
        by_art = data.get("missing_reasons_by_article") or {}
        examples: list[str] = []
        for art, lst in by_art.items() if isinstance(by_art, dict) else []:
            for it in lst or []:
                path = it.get("path") if isinstance(it, dict) else None
                reason = it.get("reason") if isinstance(it, dict) else None
                if path:
                    examples.append(f"[{art}] {path} ({reason})")
                if len(examples) > 4:
                    break
            if len(examples) > 4:
                break
        if examples:
            lines.append("- missing_examples:")
            for ex in examples:
                lines.append(f"  - {ex}")
        return lines
    except (json.JSONDecodeError, OSError, UnicodeDecodeError, AttributeError):
        return []
