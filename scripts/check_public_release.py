# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Public release guard for the clean Intrinsical Policy Engine distribution."""

from __future__ import annotations

import argparse
import os
import stat
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]

ALLOWED_ROOT_FILES = {
    ".gitattributes",
    ".gitignore",
    ".pre-commit-config.yaml",
    "BOUNDARIES.md",
    "CHANGELOG.md",
    "LICENSE",
    "NOTICE",
    "README.md",
    "SECURITY.md",
    "pyproject.toml",
    "uv.lock",
}

ALLOWED_PREFIXES = (
    ".github/workflows/",
    "demos/starter/",
    "demos/process-review/",
    "docs/",
    "frameworks/starter/",
    "scripts/",
    "src/",
    "tests/smoke/",
    "tests/unit/",
)

BLOCKED_SUFFIXES = (".pdf", ".docx", ".png", ".jpg", ".jpeg")
DIST_NAME = "intrinsical_policy_engine"
DIST_METADATA_FILES = {"METADATA", "WHEEL", "RECORD", "entry_points.txt", "top_level.txt"}
SDIST_METADATA_FILES = {
    "PKG-INFO",
    "SOURCES.txt",
    "dependency_links.txt",
    "entry_points.txt",
    "requires.txt",
    "top_level.txt",
}


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
    ".ini",
    ".j2",
    ".json",
    ".lock",
    ".md",
    ".py",
    ".toml",
    ".txt",
    ".yml",
    ".yaml",
}


def _git_files() -> list[Path]:
    if not (ROOT / ".git").exists():
        return []
    proc = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=ROOT,
        capture_output=True,
        check=True,
    )
    paths = [Path(os.fsdecode(name)) for name in proc.stdout.split(b"\0") if name]
    return [path for path in paths if (ROOT / path).exists() or (ROOT / path).is_symlink()]


def _walk_files() -> list[Path]:
    blocked_dirs = {
        ".git",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        ".uv-cache",
        ".venv",
        "intrinsical_policy_engine.egg-info",
        "build",
        "dist",
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
    return path.name == "py.typed" or path.suffix.lower() in TEXT_SUFFIXES


def _scan_content(rel: Path, content: bytes, label: str) -> list[str]:
    if rel.suffix.lower() in BLOCKED_SUFFIXES:
        return [f"blocked binary-like asset: {label}"]
    if not _is_text_file(rel):
        return [f"blocked file type: {label}"]
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return [f"non-utf8 text file: {label}"]
    lowered = text.lower()
    return [f"blocked term in {label}: {term}" for term in DENY_TERMS if term in lowered]


def scan_public_tree() -> list[str]:
    violations: list[str] = []
    for rel in _candidate_files():
        rel_s = rel.as_posix()
        path = ROOT / rel
        if not _is_allowed_path(rel):
            violations.append(f"blocked path: {rel_s}")
        elif path.is_symlink():
            violations.append(f"blocked symbolic link: {rel_s}")
        else:
            violations.extend(_scan_content(rel, path.read_bytes(), rel_s))
    return violations


def _archive_path(name: str) -> Path | None:
    path = PurePosixPath(name)
    if "\\" in name or path.is_absolute() or ".." in path.parts or not path.parts:
        return None
    return Path(*path.parts)


def _wheel_path(rel: Path) -> bool:
    if rel.parts[0] == DIST_NAME:
        return len(rel.parts) > 1
    root = rel.parts[0]
    if not root.startswith(f"{DIST_NAME}-") or not root.endswith(".dist-info"):
        return False
    if len(rel.parts) == 2:
        return rel.name in DIST_METADATA_FILES
    return len(rel.parts) == 3 and rel.parts[1] == "licenses" and rel.name in {"LICENSE", "NOTICE"}


def _sdist_path(rel: Path) -> bool:
    if _is_allowed_path(rel) or rel.as_posix() in {"PKG-INFO", "setup.cfg"}:
        return True
    return (
        len(rel.parts) == 2
        and rel.parts[0] == f"{DIST_NAME}.egg-info"
        and rel.name in SDIST_METADATA_FILES
    )


def _scan_wheel(path: Path) -> list[str]:
    violations: list[str] = []
    with zipfile.ZipFile(path) as archive:
        seen: set[str] = set()
        for member in archive.infolist():
            if member.is_dir():
                continue
            label = f"{path.name}:{member.filename}"
            rel = _archive_path(member.filename)
            if rel is None or not _wheel_path(rel) or member.filename in seen:
                violations.append(f"blocked archive path: {label}")
            elif stat.S_ISLNK(member.external_attr >> 16):
                violations.append(f"blocked symbolic link: {label}")
            else:
                violations.extend(_scan_content(rel, archive.read(member), label))
            seen.add(member.filename)
    return violations


def _scan_sdist(path: Path) -> list[str]:
    violations: list[str] = []
    with tarfile.open(path, "r:gz") as archive:
        seen: set[str] = set()
        roots: set[str] = set()
        for member in archive.getmembers():
            if member.isdir():
                continue
            label = f"{path.name}:{member.name}"
            full = _archive_path(member.name)
            if full is None or len(full.parts) < 2:
                violations.append(f"blocked archive path: {label}")
                continue
            roots.add(full.parts[0])
            rel = Path(*full.parts[1:])
            if (
                not full.parts[0].startswith(f"{DIST_NAME}-")
                or not _sdist_path(rel)
                or member.name in seen
            ):
                violations.append(f"blocked archive path: {label}")
            elif not member.isfile():
                violations.append(f"blocked archive entry: {label}")
            else:
                stream = archive.extractfile(member)
                if stream is None:
                    violations.append(f"unreadable archive entry: {label}")
                else:
                    with stream:
                        violations.extend(_scan_content(rel, stream.read(), label))
            seen.add(member.name)
        if len(roots) != 1:
            violations.append(f"invalid source distribution root: {path.name}")
    return violations


def scan_distribution(path: Path) -> list[str]:
    """Inspect an explicitly supplied wheel or source archive without extracting it."""
    try:
        if path.name.endswith(".whl"):
            return _scan_wheel(path)
        if path.name.endswith(".tar.gz"):
            return _scan_sdist(path)
        return [f"unsupported distribution: {path}"]
    except (OSError, tarfile.TarError, zipfile.BadZipFile, RuntimeError) as exc:
        return [f"unreadable distribution {path}: {exc}"]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--distribution",
        action="append",
        type=Path,
        default=[],
        help="wheel or .tar.gz source distribution to inspect in addition to the source tree",
    )
    args = parser.parse_args()
    violations = scan_public_tree()
    for path in args.distribution:
        violations.extend(scan_distribution(path))
    if violations:
        for item in violations:
            print(item, file=sys.stderr)
        return 1
    print("public release guard passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
