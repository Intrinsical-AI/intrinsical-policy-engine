# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Offline API payload generation with mandatory validated configuration."""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, ClassVar, Generic, TypeVar

from intrinsical_policy_engine.adapters.export.base.evidence.task_api_utils import redact_headers
from intrinsical_policy_engine.adapters.export.base.evidence.task_graph import build_task_items
from intrinsical_policy_engine.adapters.export.base.models.export_config import ApiExportConfig
from intrinsical_policy_engine.adapters.export.base.models.shapes import TaskItem
from intrinsical_policy_engine.adapters.logging import StructuredLogger
from intrinsical_policy_engine.app.export.artifacts import write_json

ApiConfig = TypeVar("ApiConfig", bound=ApiExportConfig)


class BaseApiExporter(ABC, Generic[ApiConfig]):
    target: ClassVar[str]
    config_type: type[ApiConfig]

    def __init__(self, config: ApiExportConfig, logger: StructuredLogger | None = None) -> None:
        if not isinstance(config, self.config_type):
            raise TypeError(f"Expected {self.config_type.__name__} for {self.target}")
        self._config: ApiConfig = config
        self._logger = logger

    def export(self, plan: dict[str, Any], out_dir: Path) -> None:
        self._export_items(build_task_items(plan), out_dir)

    def _export_items(self, items: list[TaskItem], out_dir: Path) -> None:
        base = self._ensure_export_dir(out_dir)
        headers = redact_headers(self._config.headers)
        self._log_safe(f"export.{self.target}.start", {"items": len(items)})
        with (base / "requests.ndjson").open("x", encoding="utf-8") as stream:
            for item in items:
                record = self.build_request_record(item, self._config, headers)
                stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        write_json(base / "index.json", {"target": self.target, "count": len(items)})
        self._log_safe(f"export.{self.target}.finish", {"items": len(items)})

    @abstractmethod
    def build_request_record(
        self, item: TaskItem, cfg: ApiConfig, headers: dict[str, str]
    ) -> dict[str, Any]:
        pass

    def _ensure_export_dir(self, out_dir: Path) -> Path:
        base = out_dir / "exports" / self.target
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _log_safe(self, event: str, data: dict[str, Any]) -> None:
        if self._logger is not None:
            self._logger.info(event, data)
