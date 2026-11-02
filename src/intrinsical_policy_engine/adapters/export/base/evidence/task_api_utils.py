# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Keep configured header values out of offline API payloads."""

from __future__ import annotations


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Replace every value; actual headers are supplied when delivering requests."""
    return dict.fromkeys(headers, "<redacted>")
