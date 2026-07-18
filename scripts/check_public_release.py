# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Public release guard for the clean Intrinsical Policy Engine distribution."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

ALLOWED_ROOT_FILES = {
    ".gitattributes",
    ".gitignore",
    ".pre-commit-config.yaml",
    "BOUNDARIES.md",
    "CHANGELOG.md",
    "LICENSE",
    "MANIFEST.in",
    "NOTICE",
    "PROVENANCE.md",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    "uv.lock",
}

ALLOWED_PREFIXES = (
    ".github/workflows/",
    "demos/starter/",
    "docs/",
    "frameworks/starter/",
    "scripts/",
    "src/",
    "tests/smoke/",
    "tests/unit/",
)

BLOCKED_SUFFIXES = (".pdf", ".docx", ".png", ".jpg", ".jpeg")


def _term(*parts: str) -> str:
    return "".join(parts)


DENY_TERMS = tuple(
    _term(*parts).lower()
    for parts in (
        ("eu", "-", "ai", "-", "act"),
        ("g", "p", "a", "i"),
        ("f", "r", "i", "a"),
        ("k", "y", "c"),
        ("d", "o", "r", "a"),
        ("n", "i", "s", "2"),
        ("ann", "ex", "III"),
        ("A", "R", "T", "-"),
        ("A", "I", "-"),
        ("role", ".", "provider"),
        ("role", ".", "deployer"),
        ("high", "_", "risk"),
        ("conform", "ity"),
        ("notified", " ", "body"),
        ("bio", "metric", "s"),
        ("credit", "_", "scoring"),
        ("bank", "ing", "_", "k", "y", "c"),
        ("hr", "_", "recruit"),
        ("legal", "_", "review"),
        ("Intrinsical", " Framework", " License"),
        ("I", "F", "L", "-", "1"),
        ("legal", "@", "intrinsical"),
        ("eu", "-", "ai", "-", "act", "-", "snapshot"),
        ("eur", "-", "lex"),
        ("32", "024", "r", "1689"),
        ("high", " ", "risk"),
        ("alto", " ", "riesgo"),
    )
)

TEXT_SUFFIXES = {
    "",
    ".cfg",
    ".csv",
    ".in",
    ".ini",
    ".j2",
    ".json",
    ".lock",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".typed",
    ".yml",
    ".yaml",
}
DISALLOWED_TEXT_CONTROL_BYTES = frozenset((*range(0, 9), 11, *range(14, 32), 127))


def _git_files() -> list[Path]:
    if not (ROOT / ".git").exists():
        return []
    proc = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        text=True,
        capture_output=True,
        check=True,
    )
    files: list[Path] = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        rel = Path(line)
        candidate = ROOT / rel
        # ``exists`` is false for dangling symlinks, which must still be scanned
        # and rejected. Deleted-but-still-indexed paths, however, are ignored.
        if candidate.exists() or candidate.is_symlink():
            files.append(rel)
    return files


def _walk_files() -> list[Path]:
    blocked_dirs = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".uv-cache",
        ".venv",
        "intrinsical_policy_engine.egg-info",
        "out",
    }
    files: list[Path] = []
    for path in ROOT.rglob("*"):
        rel = path.relative_to(ROOT)
        if any(part in blocked_dirs or part == "__pycache__" for part in rel.parts):
            continue
        if path.is_file() or path.is_symlink():
            files.append(rel)
    return files


def _candidate_files() -> list[Path]:
    return _git_files() or _walk_files()


def _is_allowed_path(rel: Path) -> bool:
    rel_s = rel.as_posix()
    return rel_s in ALLOWED_ROOT_FILES or any(
        rel_s.startswith(prefix) for prefix in ALLOWED_PREFIXES
    )


def _is_text_file(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES


def scan_public_tree() -> list[str]:
    violations: list[str] = []
    for rel in _candidate_files():
        rel_s = rel.as_posix()
        abs_path = ROOT / rel

        if abs_path.is_symlink():
            violations.append(f"blocked symlink: {rel_s}")
            continue

        if not _is_allowed_path(rel):
            violations.append(f"blocked path: {rel_s}")
            continue

        if rel.suffix.lower() in BLOCKED_SUFFIXES:
            violations.append(f"blocked binary-like asset: {rel_s}")
            continue

        if not _is_text_file(rel):
            violations.append(f"unsupported file type: {rel_s}")
            continue

        if rel_s == "scripts/check_public_release.py":
            continue

        try:
            payload = abs_path.read_bytes()
        except OSError as exc:
            violations.append(f"unreadable file: {rel_s}: {exc.__class__.__name__}")
            continue

        if any(byte in DISALLOWED_TEXT_CONTROL_BYTES for byte in payload):
            violations.append(f"binary content in text file: {rel_s}")
            continue

        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            violations.append(f"non-utf8 text file: {rel_s}")
            continue

        lowered = text.lower()
        for term in DENY_TERMS:
            if term in lowered:
                violations.append(f"blocked term in {rel_s}: {term}")
    return violations


def main() -> int:
    violations = scan_public_tree()
    if violations:
        for item in violations:
            print(item, file=sys.stderr)
        return 1
    print("public release guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
