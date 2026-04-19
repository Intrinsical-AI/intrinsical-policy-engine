# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Utilities for classifying evidence quality before export."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from src.adapters.quality.engine import QualityEngine
from src.common.text.front_matter import parse_front_matter


def dedupe_expected(raw_entries: list[Any]) -> list[dict]:
    """Normalize evidence entries by path and OR the `required` flag across duplicates."""
    by_path: dict[str, dict] = {}
    for item in raw_entries or []:
        if isinstance(item, dict) and item.get("path"):
            p = str(item.get("path"))
            req = bool(item.get("required", True))
        else:
            p = str(item)
            req = True
        cur = by_path.get(p)
        if cur is None:
            by_path[p] = {"path": p, "required": req}
        else:
            cur["required"] = bool(cur.get("required", True) or req)
    return list(by_path.values())


def compute_evidence_quality(
    *,
    base_root: Path,
    included: Iterable[str],
    selected_articles: set[str],
    ev_map: dict[str, list[dict[str, Any]]] | None,
    quality_engine: QualityEngine,
) -> dict:
    """Compute evidence quality metrics for included files.

    Returns a dict with:
    - quality_by_file: Final quality status per file (SSoT)
      Priority: front-matter "status" field > heuristic analysis
    - missing_reasons_by_article: Details on why required files are missing

    Quality values: "ready" (complete), "draft" (partial), "placeholder" (empty/structure only)
    """
    included_set = set(included or [])

    # Step 1: Compute heuristic quality for all files
    heuristic_quality: dict[str, str] = {}
    for rel in included_set:
        fp = base_root / rel
        heuristic_quality[rel] = quality_engine.classify_file(fp)

    # Step 2: Determine final quality (front-matter > heuristic)
    # This is the Single Source of Truth
    quality_by_file: dict[str, str] = {}
    for rel in included_set:
        fp = base_root / rel
        final_status: str | None = None

        try:
            # For markdown files, check front-matter first
            if fp.suffix.lower() == ".md":
                txt = fp.read_text(encoding="utf-8", errors="ignore")
                fm = parse_front_matter(txt)
                st = (fm or {}).get("status") if isinstance(fm, dict) else None
                if isinstance(st, str) and st in {"ready", "draft", "placeholder"}:
                    final_status = st
        except (OSError, UnicodeDecodeError):
            pass

        # Fallback to heuristic if no explicit status
        if final_status is None:
            hq = heuristic_quality.get(rel, "placeholder")
            final_status = (
                "ready" if hq == "ready" else ("draft" if hq == "draft" else "placeholder")
            )

        quality_by_file[rel] = final_status

    missing_reasons_by_article: dict[str, list[dict[str, str]]] = {}
    ev_map = ev_map or {}
    for art in sorted(selected_articles or set()):
        raw_entries = list(ev_map.get(art, []) or [])
        expected_entries = dedupe_expected(raw_entries)
        missing_details: list[dict[str, str]] = []
        for e in expected_entries:
            p = str(e.get("path"))
            req = bool(e.get("required", True))
            if not req:
                continue
            if p.endswith("/"):
                ok = quality_engine.dir_requirement_met(base_root, included_set, p)
                if not ok:
                    missing_details.append({"path": p, "reason": "dir_requirement_missing"})
            else:
                ok = (p in included_set) and quality_engine.is_valid_file(base_root / p)
                if not ok:
                    _ok, why = quality_engine.diagnose_file(base_root / p)
                    if _ok:
                        why = "ok"
                    missing_details.append(
                        {"path": p, "reason": (why if why != "ok" else "quality_fail")}
                    )
        if missing_details:
            missing_reasons_by_article[art] = missing_details

    return {
        "quality_by_file": quality_by_file,
        "missing_reasons_by_article": missing_reasons_by_article,
    }
