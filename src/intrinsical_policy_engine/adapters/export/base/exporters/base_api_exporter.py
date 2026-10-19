# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Offline API payload generation with mandatory validated configuration."""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Any, Generic, TypeVar

from intrinsical_policy_engine.adapters.export.base.evidence.task_api_utils import redact_headers
from intrinsical_policy_engine.adapters.export.base.evidence.task_graph import build_task_items
from intrinsical_policy_engine.adapters.export.base.models.export_config import ApiExportConfig
from intrinsical_policy_engine.adapters.export.base.models.shapes import TaskItem
from intrinsical_policy_engine.adapters.logging import StructuredLogger, adapt_logger
from intrinsical_policy_engine.app.export.artifacts import write_json

ApiConfig = TypeVar("ApiConfig", bound=ApiExportConfig)


class BaseApiExporter(ABC, Generic[ApiConfig]):
    config_type: type[ApiConfig]

    def __init__(self) -> None:
        self._logger: StructuredLogger | None = None
        self._config: ApiConfig | None = None

    def setup(
        self,
        logger: StructuredLogger | logging.Logger | None = None,
        config: ApiExportConfig | None = None,
    ) -> None:
        if not isinstance(config, self.config_type):
            raise TypeError(f"Expected {self.config_type.__name__} for {self.get_target_name()}")
        self._config = config
        self._logger = adapt_logger(logger)

    def export(self, plan: dict[str, Any], templates_dir: str, out_dir: str) -> None:
        self._export_items(build_task_items(plan), out_dir)

    def _export_items(self, items: list[TaskItem], out_dir: str) -> None:
        cfg = self._cfg()
        base = self._ensure_export_dir(out_dir, self.get_target_name())
        headers = redact_headers(cfg.headers)
        self._log_safe(f"export.{self.get_target_name()}.start", {"items": len(items)})
        with (base / "requests.ndjson").open("x", encoding="utf-8") as stream:
            for item in items:
                record = self.build_request_record(item, item.description_md, cfg, headers)
                stream.write(json.dumps(record, ensure_ascii=False, allow_nan=False) + "\n")
        write_json(base / "index.json", {"target": self.get_target_name(), "count": len(items)})
        self._log_safe(f"export.{self.get_target_name()}.finish", {"items": len(items)})

    @abstractmethod
    def get_target_name(self) -> str:
        pass

    @abstractmethod
    def build_request_record(
        self, item: TaskItem, description: str, cfg: ApiConfig, headers: dict[str, str]
    ) -> dict[str, Any]:
        pass

    def _cfg(self) -> ApiConfig:
        if self._config is None:
            raise ValueError("Call setup with validated configuration before export")
        return self._config

    def _ensure_export_dir(self, out_dir: str, target: str) -> Path:
        base = Path(out_dir) / "exports" / target
        base.mkdir(parents=True, exist_ok=True)
        return base

    def _log_safe(self, event: str, data: dict[str, Any]) -> None:
        if self._logger is not None:
            self._logger.info(event, data)
