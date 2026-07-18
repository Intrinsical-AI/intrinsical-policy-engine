# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Logging adapters (file system, structured logging helpers)."""

from intrinsical_policy_engine.adapters.logging.adapters import StdlibLoggerAdapter, adapt_logger
from intrinsical_policy_engine.adapters.logging.fs.fs_log import FsLogger
from intrinsical_policy_engine.adapters.logging.protocol import StructuredLogger

__all__ = [
    "FsLogger",
    "StdlibLoggerAdapter",
    "StructuredLogger",
    "adapt_logger",
]
