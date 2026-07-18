# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""CI Runner: Enforce compliance gates in automated pipelines.

This module implements the 'Quality Gate' logic that allows the CLI to be used
as a blocking step in CI/CD (GitHub Actions, GitLab CI).

Quality Gates:
- CI-001: Blocked outcome detection
- CI-002: Missing evidence for review actions
- CI-003: Evidence quality below threshold (word count, structure)
- CI-004: Incomplete action coverage for review systems
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, TypedDict

from intrinsical_policy_engine.domain.types import Plan

# Quality thresholds (can be overridden via config)
DEFAULT_MIN_WORDS_EVIDENCE = 50  # Minimum words in evidence file
DEFAULT_MIN_COVERAGE_PCT = 80  # Minimum % of actions with evidence
REQUIRED_SECTIONS_MARKDOWN = {"##", "###"}  # Must have at least one heading


class Finding(TypedDict):
    """Structured CI finding describing severity/code/message."""

    severity: str  # "error" | "warning"
    code: str
    msg: str


class CIRunnerConfig(TypedDict, total=False):
    """Optional configuration for quality thresholds."""

    min_words_evidence: int
    min_coverage_pct: int
    evidence_base_path: str | None


class CIRunner:
    """Executes compliance checks on a generated Plan.

    Args:
        config: Optional configuration overriding default thresholds.
    """

    def __init__(self, config: CIRunnerConfig | None = None):
        self.config = config or {}
        self.min_words = self.config.get("min_words_evidence", DEFAULT_MIN_WORDS_EVIDENCE)
        self.min_coverage = self.config.get("min_coverage_pct", DEFAULT_MIN_COVERAGE_PCT)
        self.evidence_base = self.config.get("evidence_base_path")

    def check(self, plan: Plan) -> list[Finding]:
        """Run all checks and return a list of findings."""
        findings: list[Finding] = []

        findings.extend(self._check_blocked(plan))
        findings.extend(self._check_review_gaps(plan))
        findings.extend(self._check_evidence_quality(plan))
        findings.extend(self._check_action_coverage(plan))

        return findings

    def _check_blocked(self, plan: Plan) -> list[Finding]:
        """Fail if the system is classified as blocked."""
        outcomes = set(plan.get("outcome") or [])
        if "blocked" in outcomes:
            return [
                {
                    "severity": "error",
                    "code": "CI-001",
                    "msg": "CRITICAL: System triggers a blocked outcome. Deployment blocked.",
                }
            ]
        return []

    def _check_review_gaps(self, plan: Plan) -> list[Finding]:
        """Fail if a Review system has actions with missing evidence."""
        outcomes = set(plan.get("outcome") or [])

        # Detect Review (Provider or Deployer) - tokens are
        # review_provider, review_deployer, etc.
        is_review = any(o.startswith("review") for o in outcomes)
        if not is_review:
            return []

        findings: list[Finding] = []
        actions = plan.get("actions", [])
        # Use actions_evidence_map which maps ActionID -> list[FilePaths]
        ev_map = plan.get("actions_evidence_map", {})

        # Get actions that have metadata (real actions, not synthetic)
        actions_meta = plan.get("actions_meta", [])
        real_action_ids = {a.get("id") for a in actions_meta if isinstance(a, dict)}

        for aid in actions:
            # Skip synthetic sentinel actions (e.g., A_STOP from stop rules)
            if aid == "A_STOP" or aid not in real_action_ids:
                continue
            evs = ev_map.get(aid, [])
            if not evs:
                findings.append(
                    {
                        "severity": "error",
                        "code": "CI-002",
                        "msg": (f"Missing evidence for required action '{aid}' in Review system."),
                    }
                )

        return findings

    def _check_evidence_quality(self, plan: Plan) -> list[Finding]:
        """Check that evidence files meet minimum quality standards.

        Quality checks:
        - Minimum word count (not just empty/stub files)
        - Has at least one markdown heading (structured content)
        """
        findings: list[Finding] = []

        if not self.evidence_base:
            return findings  # Skip if no base path configured

        outcomes = set(plan.get("outcome") or [])
        is_review = any(o.startswith("review") for o in outcomes)
        if not is_review:
            return findings  # Only enforce for review

        ev_map = plan.get("actions_evidence_map", {})
        base_path = Path(self.evidence_base)

        for _aid, ev_paths in ev_map.items():
            for ev_path in ev_paths:
                full_path = base_path / ev_path
                if not full_path.exists():
                    continue

                try:
                    content = full_path.read_text(encoding="utf-8")
                    word_count = len(content.split())

                    # Check minimum word count
                    if word_count < self.min_words:
                        findings.append(
                            {
                                "severity": "warning",
                                "code": "CI-003",
                                "msg": (
                                    f"Evidence '{ev_path}' has only {word_count} words "
                                    f"(minimum: {self.min_words}). May be incomplete."
                                ),
                            }
                        )

                    # Check for structure (markdown headings)
                    if ev_path.endswith(".md"):
                        has_headings = any(
                            line.strip().startswith(h)
                            for line in content.split("\n")
                            for h in REQUIRED_SECTIONS_MARKDOWN
                        )
                        if not has_headings and word_count > 10:
                            findings.append(
                                {
                                    "severity": "warning",
                                    "code": "CI-003",
                                    "msg": (
                                        f"Evidence '{ev_path}' lacks markdown structure "
                                        "(no ## or ### headings found)."
                                    ),
                                }
                            )

                except (OSError, UnicodeDecodeError) as err:
                    # R1 Fix: Register explicit finding for unreadable evidence
                    findings.append(
                        {
                            "severity": "warning",
                            "code": "CI-READ",
                            "msg": (
                                f"Cannot read evidence '{ev_path}': {type(err).__name__}. "
                                "File may be binary, corrupted, or inaccessible."
                            ),
                        }
                    )

        return findings

    def _check_action_coverage(self, plan: Plan) -> list[Finding]:
        """Check that enough actions have evidence (coverage metric).

        For review systems, at least min_coverage_pct of actions
        should have at least one evidence file mapped.
        """
        findings: list[Finding] = []

        outcomes = set(plan.get("outcome") or [])
        is_review = any(o.startswith("review") for o in outcomes)
        if not is_review:
            return findings

        actions = plan.get("actions", [])
        if not actions:
            return findings

        # Filter out synthetic actions (A_STOP, etc.) for coverage calculation
        actions_meta = plan.get("actions_meta", [])
        real_action_ids = {a.get("id") for a in actions_meta if isinstance(a, dict)}
        real_actions = [aid for aid in actions if aid in real_action_ids]

        if not real_actions:
            return findings

        ev_map = plan.get("actions_evidence_map", {})
        actions_with_evidence = sum(1 for aid in real_actions if ev_map.get(aid))
        coverage_pct = (actions_with_evidence / len(real_actions)) * 100

        if coverage_pct < self.min_coverage:
            findings.append(
                {
                    "severity": "error",
                    "code": "CI-004",
                    "msg": (
                        f"Evidence coverage is {coverage_pct:.1f}% "
                        f"(minimum: {self.min_coverage}%). "
                        f"{len(real_actions) - actions_with_evidence} actions missing evidence."
                    ),
                }
            )
        elif coverage_pct < 100:
            # Warning if not 100% but above threshold
            findings.append(
                {
                    "severity": "warning",
                    "code": "CI-004",
                    "msg": (
                        f"Evidence coverage is {coverage_pct:.1f}%. "
                        f"{len(real_actions) - actions_with_evidence} actions "
                        "without mapped evidence."
                    ),
                }
            )

        return findings

    def get_coverage_stats(self, plan: Plan) -> dict[str, Any]:
        """Return coverage statistics for reporting."""
        actions = plan.get("actions", [])
        ev_map = plan.get("actions_evidence_map", {})

        # Filter out synthetic actions (A_STOP, etc.)
        actions_meta = plan.get("actions_meta", [])
        real_action_ids = {a.get("id") for a in actions_meta if isinstance(a, dict)}
        real_actions = [aid for aid in actions if aid in real_action_ids]

        if not real_actions:
            return {"total_actions": 0, "with_evidence": 0, "coverage_pct": 0.0}

        with_evidence = sum(1 for aid in real_actions if ev_map.get(aid))
        return {
            "total_actions": len(real_actions),
            "with_evidence": with_evidence,
            "without_evidence": len(real_actions) - with_evidence,
            "coverage_pct": (with_evidence / len(real_actions)) * 100,
        }
