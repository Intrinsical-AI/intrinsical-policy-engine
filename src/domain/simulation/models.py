# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
from __future__ import annotations

import hashlib
import json
import math
import unicodedata
from dataclasses import dataclass

from src.domain.simulation.types import ChangeOrigin, JSONValue, ResidualRiskLevel


def _nfc(s: str) -> str:
    """Normalize a string to NFC for stable hashing and IDs."""
    return unicodedata.normalize("NFC", s)


def _validate_json_value(value: object, *, path: str) -> None:
    """
    Recursively validate that value is a JSONValue and that floats are finite.
    Raises ValueError or TypeError with a useful path.
    """
    # Order matters: bool is a subclass of int.
    if value is None or isinstance(value, (str, bool, int)):
        return

    if isinstance(value, float):
        if not math.isfinite(value):
            raise ValueError(f"Non-finite float at {path}")
        return

    if isinstance(value, list):
        for i, item in enumerate(value):
            _validate_json_value(item, path=f"{path}/{i}")
        return

    if isinstance(value, dict):
        for k, v in value.items():
            if not isinstance(k, str):
                raise TypeError(f"Non-string key at {path}: {type(k)}")
            _validate_json_value(v, path=f"{path}/{_nfc(k)}")
        return

    raise TypeError(f"Invalid JSONValue type at {path}: {type(value)}")


def _normalize_json_value(value: JSONValue) -> JSONValue:
    """
    Normaliza recursivamente strings (NFC) para evitar IDs distintos por equivalencia Unicode.
    """
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        return _nfc(value)
    if isinstance(value, list):
        return [_normalize_json_value(v) for v in value]
    # dict[str, JSONValue]
    return {_nfc(k): _normalize_json_value(v) for k, v in value.items()}


@dataclass(frozen=True)
class EffortMetric:
    """Agregado de esfuerzo en CU. Inmutable."""

    technical_cu: int
    bureaucratic_cu: int
    external_cu: int

    @property
    def total(self) -> int:
        return self.technical_cu + self.bureaucratic_cu + self.external_cu

    def __add__(self, other: EffortMetric) -> EffortMetric:
        return EffortMetric(
            self.technical_cu + other.technical_cu,
            self.bureaucratic_cu + other.bureaucratic_cu,
            self.external_cu + other.external_cu,
        )


@dataclass(frozen=True)
class ScenarioPatch:
    """
    Atomic change to a response.
    Note: question_id may be a path like "section/q1" or JSON-pointer-like.
    """

    question_id: str
    value: JSONValue
    origin: ChangeOrigin
    rationale: str

    def __post_init__(self) -> None:
        qid = _nfc(self.question_id)
        _validate_json_value(self.value, path=f"/patch[{qid}]/value")

        # Normaliza para estabilidad (incluye strings anidados)
        norm_val = _normalize_json_value(self.value)

        # set en dataclass frozen
        object.__setattr__(self, "question_id", qid)
        object.__setattr__(self, "value", norm_val)

    def canonical_payload(self) -> dict[str, object]:
        """
        Canonical dict used for hashing.
        rationale is excluded from the hash because it is human-only.
        """
        return {"q": self.question_id, "v": self.value, "o": self.origin}


@dataclass(frozen=True)
class SimulationResult:
    """
    Result of a counterfactual execution.
    Use build() to guarantee a stable scenario_id.
    """

    scenario_id: str
    base_plan_hash: str
    patches: tuple[ScenarioPatch, ...]
    outcome: str
    risk_tier: str
    effort: EffortMetric
    residual_risk: ResidualRiskLevel
    warnings: tuple[str, ...]

    @classmethod
    def build(
        cls,
        *,
        base_plan_hash: str,
        patches: list[ScenarioPatch],
        outcome: str,
        risk_tier: str,
        effort: EffortMetric,
        residual_risk: ResidualRiskLevel,
        warnings: list[str],
    ) -> SimulationResult:
        # Canon: orden + unicidad
        patches_tuple = tuple(sorted(patches, key=lambda p: p.question_id))

        ids = [p.question_id for p in patches_tuple]
        if len(set(ids)) != len(ids):
            # Semántica ambigua: dos cambios para el mismo question_id
            dupes = sorted({x for x in ids if ids.count(x) > 1})
            raise ValueError(f"Duplicate question_id in patches: {dupes}")

        warnings_tuple = tuple(_nfc(w) for w in warnings)

        scenario_id = cls._generate_deterministic_id(base_plan_hash, patches_tuple)

        return cls(
            scenario_id=scenario_id,
            base_plan_hash=_nfc(base_plan_hash),
            patches=patches_tuple,
            outcome=_nfc(outcome),
            risk_tier=_nfc(risk_tier),
            effort=effort,
            residual_risk=residual_risk,
            warnings=warnings_tuple,
        )

    @staticmethod
    def _generate_deterministic_id(
        base_hash: str,
        patches: tuple[ScenarioPatch, ...],
    ) -> str:
        """
        ID = SHA-256(JSON canónico estructurado) para evitar colisiones por concatenación.
        """
        payload = {
            "base": _nfc(base_hash),
            "patches": [p.canonical_payload() for p in patches],
        }

        canonical_bytes = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,  # red de seguridad adicional
        ).encode("utf-8")

        digest = hashlib.sha256(canonical_bytes).hexdigest()[:12]
        return f"SCN-{digest}"
