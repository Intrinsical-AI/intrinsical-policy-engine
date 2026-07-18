# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Quality gates for evidence validation.

Gates validate the SUBSTANCE of evidence files, not just their existence or format.
Each gate enforces domain-specific quality requirements before marking evidence as 'ready'.
"""

import csv
import io
from abc import ABC, abstractmethod
from pathlib import Path

from intrinsical_policy_engine.common.text.front_matter import parse_front_matter


class QualityGate(ABC):
    """Abstract base class for evidence quality gates."""

    @abstractmethod
    def check(self, evidence_path: Path) -> tuple[bool, str]:
        """Check if evidence meets quality requirements.

        Args:
            evidence_path: Path to evidence file

        Returns:
            Tuple of (is_ready, reason_code)
                is_ready: True if evidence passes quality gate
                reason_code: "ready" if passed, or specific failure reason
        """
        pass


class HITLQualityGate(QualityGate):
    """Validates HITL policy has substantive training evidence.

    Enforces:
    1. HITL policy front matter status is 'ready' or 'approved'
    2. Training evidence CSV exists (prv.hitl.a14.training.v1.csv)
    3. CSV has actual data rows (not just header)
    4. Data rows are not empty/ghost records
    """

    def check(self, evidence_path: Path) -> tuple[bool, str]:
        """Ensure HITL policy file and associated CSV provide substantive data."""
        # Gate 1: Validate HITL policy metadata
        try:
            content = evidence_path.read_text(encoding="utf-8")
            fm = parse_front_matter(content) or {}
        except (OSError, UnicodeDecodeError):
            return False, "hitl_md_parse_error"

        # Check status (use "ready" or "approved" consistently with your front matter schema)
        status = fm.get("status")
        if status not in {"ready", "approved"}:
            return False, f"hitl_status_not_ready_({status})"

        # Gate 2: Locate training evidence satellite file
        training_csv = evidence_path.parent / "prv.hitl.a14.training.v1.csv"
        if not training_csv.exists():
            return False, "hitl_missing_training_csv"

        # Gate 3: Validate substantive CSV content
        try:
            csv_content = training_csv.read_text(encoding="utf-8").strip()
            if not csv_content:
                return False, "hitl_training_empty_file"

            # Use proper CSV parser to avoid false positives with newlines
            f = io.StringIO(csv_content)
            reader = csv.reader(f)
            rows = list(reader)

            # Rule: Header + At least 1 valid data row
            if len(rows) < 2:
                return False, "hitl_training_no_data_rows"

            # Optional: Validate first data row has non-empty cells
            first_data_row = rows[1]
            if not any(cell.strip() for cell in first_data_row):
                return False, "hitl_training_ghost_data"

        except UnicodeDecodeError:
            return False, "hitl_training_binary_garbage"
        except (OSError, csv.Error):
            return False, "hitl_csv_parse_error"

        return True, "ready"


class ImpactReviewQualityGate(QualityGate):
    """Validates impact_review template is substantively filled, not placeholder.

    Enforces:
    1. No placeholder markers (TO BE COMPLETED, [PLACEHOLDER])
    2. Minimum content length (>500 chars as heuristic)
    """

    PLACEHOLDER_PATTERNS = {"TO BE COMPLETED", "[PLACEHOLDER]", "TODO:", "FIXME:"}
    MIN_CONTENT_LENGTH = 500

    def check(self, evidence_path: Path) -> tuple[bool, str]:
        """Enforce minimum content and absence of placeholders."""
        try:
            content = evidence_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False, "impact_review_read_error"

        # Check for placeholder patterns
        for pattern in self.PLACEHOLDER_PATTERNS:
            if pattern in content:
                return False, f"impact_review_has_placeholders_{pattern.replace(' ', '_').lower()}"

        # Check minimum content length
        if len(content.strip()) < self.MIN_CONTENT_LENGTH:
            return False, "impact_review_too_short"

        return True, "ready"
