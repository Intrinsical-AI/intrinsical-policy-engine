# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Filesystem-backed plan store with append-only index."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from src.app.config.artifact_names import INDEX_FILE, PLANS_DIR
from src.app.config.constants import DEFAULT_ENCODING
from src.common.io_safety import acquire_lock, atomic_write_text
from src.domain.ports import PlanStorePort


class FsPlanStore(PlanStorePort):
    """Persist plan JSON blobs and maintain a durable index."""

    def __init__(self, base_dir: str = "out") -> None:
        """Configure plan base directory and bootstrap the index file."""
        self.base = Path(base_dir) / PLANS_DIR
        self.base.mkdir(parents=True, exist_ok=True)
        self.idx = self.base / INDEX_FILE
        if not self.idx.exists():
            # Initial atomic create
            atomic_write_text(self.idx, "[]", encoding=DEFAULT_ENCODING)

    def save(self, plan_id: str, data: dict[str, Any]) -> None:
        """Persist the plan JSON and append metadata to index.json."""
        # Persist plan
        p = self.base / f"{plan_id}.json"
        payload = json.dumps(data, ensure_ascii=False, indent=2)
        atomic_write_text(p, payload, encoding=DEFAULT_ENCODING)

        # Fingerprint
        h = hashlib.sha256(payload.encode(DEFAULT_ENCODING)).hexdigest()
        atomic_write_text(self.base / f"{plan_id}.sha256", h, encoding=DEFAULT_ENCODING)

        # Index update with lock
        ts = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
        entry = {"plan_id": plan_id, "path": p.name, "sha256": h, "created_at": ts}

        lock_path = self.base / f"{INDEX_FILE}.lock"
        logger = logging.getLogger(__name__)

        # R1 Fix: Retry with exponential backoff instead of single attempt
        max_retries = 5
        base_backoff = 0.1  # seconds

        for attempt in range(max_retries):
            try:
                with acquire_lock(lock_path, timeout=2.0):
                    try:
                        current_content = self.idx.read_text(encoding=DEFAULT_ENCODING)
                        index = json.loads(current_content)
                    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
                        index = []

                    if not isinstance(index, list):
                        index = []

                    index.append(entry)
                    atomic_write_text(
                        self.idx,
                        json.dumps(index, ensure_ascii=False, indent=2),
                        encoding=DEFAULT_ENCODING,
                    )
                    return  # Success
            except BlockingIOError:
                if attempt < max_retries - 1:
                    import time

                    backoff = base_backoff * (2**attempt)
                    logger.debug(
                        "fs_store.index.lock_retry",
                        extra={
                            "attempt": attempt + 1,
                            "max_retries": max_retries,
                            "backoff_seconds": backoff,
                        },
                    )
                    time.sleep(backoff)

        # All retries exhausted
        logger.warning(
            "fs_store.index.lock_exhausted",
            extra={
                "base_dir": str(self.base),
                "index_path": str(self.idx),
                "lock_path": str(lock_path),
                "plan_id": plan_id,
                "attempts": max_retries,
            },
        )
        # Plan is persisted, but index entry is lost - log for reconciliation
