# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Entry point for `python -m src.app.cli`."""

import sys

from src.app.cli.main import main

if __name__ == "__main__":
    sys.exit(main())
