# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Centralized constants used across CLI/UI/export subsystems."""

from __future__ import annotations

# File I/O constants
DEFAULT_ENCODING = "utf-8"
JSON_MIME_TYPE = "application/json"
HTML_MIME_TYPE = "text/html"

# Boolean string representations for environment variables
TRUE_VALUES = {"1", "true", "yes"}
FALSE_VALUES = {"0", "false", "no"}

# UI timeouts and delays (in milliseconds)
UI_COPY_TIMEOUT_MS = 1200
UI_INPUT_DELAY_MS = 150
UI_ANIMATION_DURATION_MS = 600

# UI sizes and limits
UI_MAX_WIDTH_PX = 1200
UI_BORDER_RADIUS_PX = 8
UI_BUTTON_PADDING_PX = 14
UI_CARD_PADDING_PX = 12

# Content limits


# Cache sizes
CACHE_MAXSIZE_SMALL = 8
CACHE_MAXSIZE_MEDIUM = 1024
CACHE_MAXSIZE_LARGE = 4096

# Time constants (seconds)
LOCKFILE_TIMEOUT_SECONDS = 300
DEFAULT_REQUEST_TIMEOUT = 30

# Validation constants
MIN_PASSWORD_LENGTH = 8
MAX_FILENAME_LENGTH = 255

# HTTP status codes
HTTP_OK = 200
HTTP_BAD_REQUEST = 400
HTTP_UNAUTHORIZED = 401
HTTP_FORBIDDEN = 403
HTTP_NOT_FOUND = 404
HTTP_INTERNAL_ERROR = 500

# Security constants
CSRF_TOKEN_LENGTH = 24
CSRF_SECRET_BYTES = 32
SESSION_TIMEOUT_MINUTES = 60

# Algorithm constants
CACHE_MAXSIZE_ASSESSMENT = 128


# Search limits
MAX_DIRECTORY_SEARCH_DEPTH = 16

# UI spacing constants (pixels)
UI_HEADER_PADDING_Y = 20
UI_HEADER_PADDING_X = 24

# File size limits (bytes)
MAX_UPLOAD_SIZE_MB = 10
MAX_JSON_SIZE_KB = 1024
MAX_ANSWERS_SIZE_BYTES = 1 * 1024 * 1024  # 1MB limit for answers file

EXPORTS_DIR = "exports"
REQUESTS_NDJSON = "requests.ndjson"
INDEX_JSON = "index.json"
EVIDENCE_ZIP = f"{EXPORTS_DIR}/evidence.zip"
EVIDENCE_MANIFEST = "evidence_manifest.json"
FINGERPRINT_JSON = "fingerprint.json"
ICS_FILE = "compliance.ics"
BACKLOG_CSV = "backlog.csv"
SUMMARY_JSON = "summary.json"
PLAN_BACKLOG_MD = "plan_backlog.md"
EVIDENCE_MAP_YML = "evidence_map.yml"
TRACE_JSONL = "trace.jsonl"
MANIFEST_MD = "manifest.md"
TRACE_JSON = "trace.json"
WIZARD_ANSWERS_JSON = "wizard_answers.json"
METRICS_JSON = "metrics.json"

# O-04: Porcelain vs Plumbing separation (TREE-SPECS-v1.md)
# Machine-readable artifacts go to METADATA_DIR, human-readable stay in root
METADATA_DIR = "_metadata"

# Files that MUST go to METADATA_DIR (machine-readable, not for humans)
PLUMBING_FILES: frozenset[str] = frozenset(
    {
        "summary.json",
        "trace.json",
        "trace.jsonl",
        "wizard_answers.json",
        "evidence_quality.json",
        "evidence_manifest.json",
        "actions.json",
        "metrics.json",
    }
)

# NOTE: UI templates (PAGE_TEMPLATE, PARTIAL_TEMPLATE) are defined in
# intrinsical_policy_engine.adapters.ui.templates and should be imported from there directly.
# This module should NOT re-export UI-layer symbols to maintain clean
# architecture (config layer must not depend on presentation layer).
