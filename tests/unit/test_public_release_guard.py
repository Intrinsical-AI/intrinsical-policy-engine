# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.

from scripts.check_public_release import scan_public_tree


def test_public_release_guard_accepts_current_tree() -> None:
    violations = scan_public_tree()
    assert violations == []
