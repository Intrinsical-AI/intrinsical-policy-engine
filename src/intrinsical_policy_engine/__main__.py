# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Module entry point for ``python -m intrinsical_policy_engine``."""

from intrinsical_policy_engine.app.cli.main import main

if __name__ == "__main__":
    raise SystemExit(main())
