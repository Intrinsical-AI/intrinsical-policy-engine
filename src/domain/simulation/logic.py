# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
from __future__ import annotations

from typing import Any

from src.domain.simulation.models import EffortMetric, ScenarioPatch
from src.domain.simulation.types import OutcomeType, ResidualRiskLevel


def normalize_outcome(outcome: Any) -> OutcomeType:
    """
    Normalize legacy outcomes (str/list/etc.) to the canonical OutcomeType.
    Fail closed softly: return "unknown" if the value cannot be mapped.
    """
    if isinstance(outcome, str):
        s = outcome.strip().lower()
        # Common normalizations.
        s = s.replace(" ", "_").replace("-", "_")
        if s in {"blocked", "ban", "banned"}:
            return "blocked"
        if s in {"review", "highrisk"}:
            return "review"
        if s in {"limited_risk", "limitedrisk"}:
            return "limited_risk"
        if s in {"out_of_scope", "outofscope", "oos"}:
            return "out_of_scope"
        return "unknown"

    if isinstance(outcome, list):
        # preferencia por lo más severo si viene en lista
        lowered = {str(x).strip().lower().replace(" ", "_").replace("-", "_") for x in outcome}
        if "blocked" in lowered:
            return "blocked"
        if "review" in lowered:
            return "review"
        if "limited_risk" in lowered:
            return "limited_risk"
        if "out_of_scope" in lowered:
            return "out_of_scope"
        return "unknown"

    return "unknown"


def compute_plan_effort(
    plan: dict[str, Any], *, strict: bool = False
) -> tuple[EffortMetric, list[str]]:
    """
    Deterministic sum based on active actions.
    Fail noisy: return aggregated warnings.
    """
    tech = doc = ext = 0
    warnings: list[str] = []

    actions_meta = plan.get("actions_meta")

    if not isinstance(actions_meta, list):
        msg = "Plan malformed: 'actions_meta' is missing or not a list."
        if strict:
            raise ValueError(msg)
        warnings.append(msg)
        return EffortMetric(0, 0, 0), warnings

    missing_effort = 0
    heuristic_map = {"S": 4, "M": 16, "L": 40, "XL": 100}

    for action in actions_meta:
        if not isinstance(action, dict):
            continue

        effort = action.get("effort")
        effort_tshirt = action.get("effort_t_shirt")
        if not isinstance(effort, dict):
            if effort_tshirt:
                hours = heuristic_map.get(str(effort_tshirt).upper(), 16)
                tech += hours // 2
                doc += hours // 2
                continue
            missing_effort += 1
            continue

        try:
            t = int(effort.get("technical", 0))
            d = int(effort.get("documentation", 0))
            e = int(effort.get("external", 0))
        except (TypeError, ValueError):
            if effort_tshirt:
                hours = heuristic_map.get(str(effort_tshirt).upper(), 16)
                tech += hours // 2
                doc += hours // 2
                continue
            missing_effort += 1
            continue

        # If effort is all-zero and we have a t-shirt size, use the heuristic.
        if t == 0 and d == 0 and e == 0 and effort_tshirt:
            hours = heuristic_map.get(str(effort_tshirt).upper(), 16)
            tech += hours // 2
            doc += hours // 2
            continue

        # Fail closed.
        if t < 0 or d < 0 or e < 0:
            if effort_tshirt:
                hours = heuristic_map.get(str(effort_tshirt).upper(), 16)
                tech += hours // 2
                doc += hours // 2
                continue
            missing_effort += 1
            continue

        tech += t
        doc += d
        ext += e

    if missing_effort > 0:
        msg = (
            f"Effort may be underestimated: {missing_effort} action(s) "
            "missing/invalid effort metadata."
        )
        if strict:
            raise ValueError(msg)
        warnings.append(msg)

    return EffortMetric(tech, doc, ext), warnings


def compute_residual_risk(
    plan: dict[str, Any],
    patches: tuple[ScenarioPatch, ...],
    ambiguity_signals: list[str],
) -> ResidualRiskLevel:
    """
    Calculate deterministic residual risk using a simple heuristic.
    """
    score = 0

    # 1) Penalty per forced patches (assumption packs).
    forced = sum(1 for p in patches if p.origin == "forced_assumption")
    score += forced * 2

    # 2) Penalty for ambiguity signals.
    score += len(ambiguity_signals)

    # 3) Penalty for high impact outcomes.
    outcome = normalize_outcome(plan.get("outcome"))
    if outcome == "blocked":
        score += 5

    if score == 0:
        return "LOW"
    if score <= 2:
        return "MEDIUM"
    if score <= 5:
        return "HIGH"
    return "CRITICAL"
