# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Logging adapters (file system, structured logging helpers)."""

from src.adapters.logging.adapters import StdlibLoggerAdapter, adapt_logger
from src.adapters.logging.fs.fs_log import FsLogger
from src.adapters.logging.protocol import StructuredLogger

__all__ = [
    "FsLogger",
    "StructuredLogger",
    "StdlibLoggerAdapter",
    "adapt_logger",
]
