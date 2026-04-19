# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Artifact rendering engine for template-based document generation."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from src.adapters.frameworks.layout_loader import maybe_load_framework_layout
from src.app.config.context import build_artifact_context
from src.app.rendering.templating import fill
from src.common.constants import CANONICAL_ENGINE_NAME, CANONICAL_ENGINE_VERSION
from src.common.hashing import sha256_file
from src.common.jinja_env import create_jinja_env, make_tracking_undefined
from src.domain.services.artifact_selector import (
    ARTIFACT_SELECTOR,
    get_active_evidence_paths,
)

# File extension categories
RENDERABLE_EXTENSIONS = {".j2", ".md", ".yaml", ".yml", ".csv", ".json"}
BINARY_EXTENSIONS = {".pdf", ".docx"}
COPY_AS_IS_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".svg", ".txt"}

# Manifest constants
DISCLAIMER_VERSION = "1.3"

# Internal paths to exclude from client package by default
INTERNAL_PREFIXES = ("_",)
INTERNAL_FILES = {"TODO.md", "todo.md"}


@dataclass(frozen=True)
class RenderConfig:
    """Configuration for artifact rendering.

    Security note:
        strict=True by default to prevent generating "valid-looking" artifacts
        with missing critical fields. Use strict=False only for local authoring.
    """

    templates_dir: Path
    plan_path: Path
    out_dir: Path
    strict: bool = True
    include_internals: bool = False
    quality_report: dict[str, Any] | None = None  # Reserved for future use


@dataclass
class RenderResult:
    """Result of a rendering operation.

    Semantics:
        - errors: hard failures (strict-mode violations, crashes, parse errors)
        - warnings: soft issues (validation issues in non-strict mode)
        - missing_fields: unique missing template vars seen across all rendered files
        - missing_fields_by_file: per-file missing vars
        - incomplete_files: files with missing vars and/or validation issues
    """

    files_rendered: int = 0
    files_copied: int = 0
    files_skipped: int = 0

    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    missing_fields: list[str] = field(default_factory=list)
    missing_fields_by_file: dict[str, list[str]] = field(default_factory=dict)
    incomplete_files: list[str] = field(default_factory=list)

    @property
    def success(self) -> bool:
        """True if no hard errors occurred."""
        return not self.errors

    @property
    def is_complete(self) -> bool:
        """True if no errors, warnings, missing vars, or incomplete files exist."""
        return (
            not self.errors
            and not self.warnings
            and not self.missing_fields
            and not self.incomplete_files
        )


class ArtifactRenderer:
    """Renders Jinja2 templates into compliance artifacts."""

    def __init__(self, config: RenderConfig) -> None:
        self.config = config
        self._layout = maybe_load_framework_layout(config.templates_dir)
        self._raw_plan: dict[str, Any] = {}
        self._ctx: dict[str, Any] = {}
        self._active_roles: set[str] = set()
        self._active_evidence: set[str] = set()
        self._answers_sha256: str | None = None
        self._wizard_answers: dict[str, Any] = {}

        # Missing tracking
        self._missing_global: set[str] = set()

    def _validate_inputs(self, result: RenderResult) -> bool:
        """Validate render inputs. Returns False if validation fails."""
        if not self.config.templates_dir.exists():
            result.errors.append(f"Templates directory not found: {self.config.templates_dir}")
            return False

        if not self.config.plan_path.exists():
            result.errors.append(f"Plan file not found: {self.config.plan_path}")
            return False

        return True

    def _prepare_context(self) -> None:
        """Load plan and prepare rendering context."""
        self._load_plan()
        self._load_wizard_answers()
        self._build_context()
        self._inject_audit_info()

        flags = self._raw_plan.get("flags", [])
        self._active_roles = ARTIFACT_SELECTOR.get_active_roles(flags)
        self._active_evidence = get_active_evidence_paths(self._raw_plan)

    def _render_root(self) -> Path:
        """Return the directory whose files should be materialized into output."""
        if (
            self._layout is not None
            and self.config.templates_dir.resolve() == self._layout.templates_dir.resolve()
        ):
            return self._layout.render_artifacts_dir
        return self.config.templates_dir

    def _should_skip_path(self, rel: Path) -> bool:
        """Check if a path should be skipped based on internals and selectors."""
        if not self.config.include_internals and _is_internal_path(rel):
            return True

        if not self.config.include_internals:
            selection = ARTIFACT_SELECTOR.should_include_evidence_path(
                rel, self._active_roles, self._active_evidence
            )
            return not selection.should_include

        return False

    def _process_missing_fields(
        self, missing_for_file: list[str], rel: Path, result: RenderResult
    ) -> bool:
        """Process missing fields. Returns False if should stop rendering."""
        if not missing_for_file:
            return True

        self._missing_global.update(missing_for_file)
        result.missing_fields_by_file[str(rel)] = missing_for_file
        result.incomplete_files.append(str(rel))

        if self.config.strict:
            for name in missing_for_file:
                result.errors.append(f"Missing field in {rel}: {name}")
            return False

        return True

    def _process_frontmatter_validation(
        self, rendered: str, ext: str, rel: Path, result: RenderResult
    ) -> bool:
        """Process frontmatter validation. Returns False if should stop rendering."""
        if ext != ".md":
            return True

        fm_issues = _validate_frontmatter(
            rendered=rendered,
            rel_path=rel,
            require_frontmatter=_frontmatter_required(rel),
        )

        if not fm_issues:
            return True

        if self.config.strict:
            result.errors.extend(fm_issues)
            result.incomplete_files.append(str(rel))
            return False

        result.warnings.extend(fm_issues)
        result.incomplete_files.append(str(rel))
        return True

    def _render_template_file(
        self,
        path: Path,
        rel: Path,
        target: Path,
        env,
        per_file_missing: set[str],
        result: RenderResult,
    ) -> bool:
        """Render a single template file. Returns False if should stop rendering."""
        target = _strip_j2_suffix(target)
        per_file_missing.clear()

        try:
            template = env.get_template(str(rel).replace("\\", "/"))
            rendered = template.render(**self._ctx)

            missing_for_file = sorted(per_file_missing)
            missing_for_file.extend(_detect_missing_markers(rendered))
            missing_for_file = sorted(set(missing_for_file))

            if not self._process_missing_fields(missing_for_file, rel, result):
                return False

            ext = path.suffix.lower()
            if not self._process_frontmatter_validation(rendered, ext, rel, result):
                return False

            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered, encoding="utf-8")
            result.files_rendered += 1

        except Exception as e:  # noqa: BLE001
            result.errors.append(f"Failed to render {rel}: {type(e).__name__}: {e}")
            result.incomplete_files.append(str(rel))
            if self.config.strict:
                return False

        return True

    def render(self) -> RenderResult:
        """Execute the full rendering pipeline.

        Returns:
            RenderResult including stats and diagnostics.
        """
        result = RenderResult()

        if not self._validate_inputs(result):
            return result

        self._prepare_context()
        self.config.out_dir.mkdir(parents=True, exist_ok=True)

        # Missing tracking collector (authoring mode only)
        per_file_missing: set[str] = set()
        undefined_cls = None
        if not self.config.strict:
            undefined_cls = make_tracking_undefined(per_file_missing)

        # Create Jinja environment
        env = create_jinja_env(
            self.config.templates_dir,
            strict=self.config.strict,
            autoescape=False,
            undefined_cls=undefined_cls,
        )
        env.globals["fill"] = fill

        render_root = self._render_root()
        templates_root = self.config.templates_dir.resolve()

        # Render all templates (sorted for deterministic output)
        for path in sorted(render_root.rglob("*")):
            source_rel = path.relative_to(templates_root)
            output_rel = path.relative_to(render_root)
            target = self.config.out_dir / output_rel

            if self._should_skip_path(source_rel):
                result.files_skipped += 1
                continue

            if path.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                continue

            ext = path.suffix.lower()

            if ext in BINARY_EXTENSIONS or ext in COPY_AS_IS_EXTENSIONS:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, target)
                result.files_copied += 1
                continue

            if ext in RENDERABLE_EXTENSIONS:
                if not self._render_template_file(
                    path, source_rel, target, env, per_file_missing, result
                ):
                    return result
                continue

            # Default: copy as-is
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target)
            result.files_copied += 1

        # finalize missing_fields list
        result.missing_fields = sorted(self._missing_global)
        return result

    def _load_plan(self) -> None:
        """Load the plan JSON file."""
        self._raw_plan = json.loads(self.config.plan_path.read_text(encoding="utf-8"))

    def _load_wizard_answers(self) -> None:
        """Load wizard/answers file for traceability.

        Security:
            Only loads from the plan's immediate directory to prevent
            accidentally loading stale answers from a different run.
        """
        plan_dir = self.config.plan_path.parent
        for name in ("wizard_answers.json", "answers.json", "answers.yml"):
            candidate = plan_dir / name
            if not candidate.exists():
                continue

            try:
                if name.endswith(".json"):
                    wizard_data = json.loads(candidate.read_text(encoding="utf-8"))
                else:
                    import yaml

                    wizard_data = yaml.safe_load(candidate.read_text(encoding="utf-8"))

                if isinstance(wizard_data, dict):
                    self._wizard_answers = wizard_data
                    self._answers_sha256 = self._generate_deterministic_answers_hash()
                    return

            except (json.JSONDecodeError, OSError):
                continue

    def _generate_deterministic_answers_hash(self) -> str:
        """Generate a deterministic hash of the current wizard answers."""
        data: Any = self._wizard_answers or {}
        if isinstance(data, dict) and "answers" in data and len(data) == 1:
            data = data["answers"]

        try:
            canonical = json.dumps(
                data,
                sort_keys=True,
                ensure_ascii=False,
                separators=(",", ":"),
            )
            return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        except (TypeError, ValueError):
            return hashlib.sha256(b"{}").hexdigest()

    def _build_context(self) -> None:
        """Build the Jinja context from the plan."""
        framework_path = (
            self._layout.framework_dir
            if self._layout is not None
            else self.config.templates_dir.parent
        )
        self._ctx = build_artifact_context(
            self._raw_plan if isinstance(self._raw_plan, dict) else {},
            strict=self.config.strict,
            framework_path=framework_path,
        )

    def _inject_audit_info(self) -> None:
        """Inject audit hashes and engine info into context."""
        rules_path = Path()
        ev_map_path = Path()
        engine_name = CANONICAL_ENGINE_NAME
        if self._layout is not None:
            rules_files = self._layout.resolve_contract_files("rules")
            evidence_map_files = self._layout.resolve_contract_files("evidence_map")
            presentation_files = self._layout.resolve_runtime_files("presentation")
            if rules_files:
                rules_path = rules_files[0]
            if evidence_map_files:
                ev_map_path = evidence_map_files[0]
            if presentation_files:
                try:
                    presentation = (
                        yaml.safe_load(presentation_files[0].read_text(encoding="utf-8")) or {}
                    )
                    if isinstance(presentation, dict) and isinstance(
                        presentation.get("engine_name"), str
                    ):
                        engine_name = presentation["engine_name"]
                except (yaml.YAMLError, OSError, UnicodeDecodeError):
                    pass
        else:
            rules_path = self.config.templates_dir.parent / "config" / "rules.yml"
            ev_map_path = self.config.templates_dir.parent / "config" / "evidence_map.yml"

        self._ctx.setdefault("audit", {})

        trace = self._raw_plan.get("trace", {}) or {}
        answers_raw = trace.get("answers_raw") or {}
        answers_hash_from_trace = answers_raw.get("answers_hash")

        answers_sha256 = self._answers_sha256 or "—"
        self._ctx["audit"]["rules_sha256"] = (
            _sha256_file(rules_path) if rules_path.exists() else "—"
        )
        self._ctx["audit"]["evidence_map_sha256"] = (
            _sha256_file(ev_map_path) if ev_map_path.exists() else "—"
        )
        self._ctx["audit"]["answers_sha256"] = answers_sha256

        answers_hash_alias = self._answers_sha256 or answers_hash_from_trace or "—"
        self._ctx["audit"]["answers_hash"] = answers_hash_alias

        plan_hash = trace.get("plan_hash")
        if not plan_hash and self.config.plan_path.exists():
            plan_hash = _sha256_file(self.config.plan_path)

        self._ctx["audit"]["plan_hash"] = plan_hash or "—"
        self._ctx["audit"]["plan_sha256"] = self._ctx["audit"]["plan_hash"]

        self._ctx.setdefault("plan", {})
        self._ctx["plan"]["fingerprint"] = plan_hash or "—"

        self._ctx.setdefault("engine", {})
        self._ctx["engine"]["version"] = _get_engine_version()
        git_commit = _get_git_commit()
        self._ctx["engine"]["commit"] = git_commit if git_commit else "dev"
        self._ctx["engine"]["name"] = engine_name
        self._ctx["disclaimer_version"] = DISCLAIMER_VERSION

        self._ctx.setdefault("meta", {})
        self._ctx["meta"]["user"] = os.environ.get("USER", "Unknown User")

        # Wizard answers for templates (traceability)
        answers_data: dict[str, Any] | None = None
        if self._wizard_answers:
            answers_data_any: Any = self._wizard_answers.get("answers", self._wizard_answers)
            if isinstance(answers_data_any, dict):
                answers_data = answers_data_any

        if not answers_data:
            answers_data_any = answers_raw.get("answers_sanitized") or {}
            if isinstance(answers_data_any, dict):
                answers_data = answers_data_any

        if answers_data:
            self._ctx["wizard_answers"] = answers_data


# ---- Helpers (module-level) ----


def _is_internal_path(rel: Path) -> bool:
    """Check if a path should be excluded from client package."""
    if rel.name in INTERNAL_FILES:
        return True
    return any(part.startswith(INTERNAL_PREFIXES) for part in rel.parts)


def _strip_j2_suffix(target: Path) -> Path:
    """Remove .j2 suffix from target path (file name only)."""
    if target.name.lower().endswith(".j2"):
        new_name = target.name[:-3].rstrip(".")
        return target.with_name(new_name)
    return target


def _sha256_file(path: Path) -> str:
    """Compute SHA-256 hash of a file."""
    return sha256_file(path)


def _get_git_commit() -> str | None:
    """Get current git commit hash, or None if not in a repo."""
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return None


def _get_engine_version() -> str:
    """Get engine version from pyproject.toml or fallback."""
    try:
        from importlib.metadata import version

        return version(CANONICAL_ENGINE_NAME)
    except Exception:  # noqa: BLE001
        try:
            pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
            if pyproject.exists():
                content = pyproject.read_text(encoding="utf-8")
                for line in content.splitlines():
                    if line.strip().startswith("version"):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
        except Exception:  # noqa: BLE001
            pass
    return CANONICAL_ENGINE_VERSION


def _detect_missing_markers(content: str) -> list[str]:
    """Detect missing fields marked by SilentEmpty (<<MISSING: key>>)."""
    import re

    pattern = r"<<MISSING:\s*([^>]+)>>"
    return [m.strip() for m in re.findall(pattern, content)]


def _frontmatter_required(rel_path: Path) -> bool:
    """Decide whether frontmatter is required for a given file path."""
    # Hard rule: evidence templates should always carry frontmatter metadata.
    return "evidence" in rel_path.parts and "templates" in rel_path.parts


def _extract_frontmatter_yaml(rendered: str) -> tuple[dict[str, Any] | None, str]:
    """Extract YAML frontmatter dict (if present) and body.

    Frontmatter format:
        ---
        key: value
        ...
        ---

    Returns:
        (frontmatter_dict_or_none, body_text)
    """
    text = rendered.lstrip("\ufeff")  # strip potential UTF-8 BOM
    if not text.startswith("---"):
        return None, rendered

    # Find end delimiter
    lines = text.splitlines()
    if len(lines) < 2:
        return None, rendered

    # Frontmatter must end with a standalone '---'
    end_idx = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            end_idx = i
            break

    if end_idx is None:
        return None, rendered

    fm_text = "\n".join(lines[1:end_idx]).strip()
    body = "\n".join(lines[end_idx + 1 :])

    try:
        import yaml

        fm = yaml.safe_load(fm_text) if fm_text else {}
        if fm is None:
            fm = {}
        if not isinstance(fm, dict):
            return None, rendered
        return fm, body
    except Exception:  # noqa: BLE001
        return None, rendered


def _validate_frontmatter(
    *,
    rendered: str,
    rel_path: Path,
    require_frontmatter: bool,
) -> list[str]:
    """Validate YAML frontmatter after rendering.

    Checks:
        - If required, frontmatter exists.
        - Required keys by category.
        - Basic type checks and date checks.
        - Status enum sanity.

    Returns:
        List of issue messages (empty if valid/acceptable).
    """
    issues: list[str] = []

    fm, _body = _extract_frontmatter_yaml(rendered)

    if fm is None:
        if require_frontmatter:
            msg = f"{rel_path}: Missing YAML frontmatter (required for evidence templates)."
            issues.append(msg)
        return issues

    category = str(fm.get("category", "template")).strip().lower()

    # Base required fields
    required: dict[str, tuple[type, ...]] = {
        "title": (str,),
        "status": (str,),
    }

    # Category-specific required fields (loose typing where it makes sense)
    category_required: dict[str, dict[str, tuple[type, ...]]] = {
        "template": {"created_at": (str,)},
        "evidence": {"created_at": (str,), "owner": (str,), "version": (str, int, float)},
        "procedure": {"created_at": (str,), "owner": (str,)},
        "policy": {"created_at": (str,), "owner": (str,)},
    }

    required.update(category_required.get(category, {"created_at": (str,)}))

    for key, types in required.items():
        if key not in fm:
            issues.append(f"{rel_path}: Missing required frontmatter field: {key}")
            continue
        if not isinstance(fm[key], types):
            expected = ", ".join(t.__name__ for t in types)
            issues.append(
                f"{rel_path}: Field '{key}' has wrong type: expected {expected}, "
                f"got {type(fm[key]).__name__}"
            )

    # status enum sanity
    status = str(fm.get("status", "")).strip().lower()
    valid_statuses = {"draft", "ready", "review", "deprecated"}
    if status and status not in valid_statuses:
        issues.append(
            f"{rel_path}: status '{status}' is not in valid set: {sorted(valid_statuses)}"
        )

    # created_at validation (ISO date) if present and not placeholder
    created_at = fm.get("created_at")
    if isinstance(created_at, str):
        s = created_at.strip()
        if s.startswith("<<MISSING:"):
            issues.append(f"{rel_path}: created_at unresolved (missing marker).")
        elif s and s != "YYYY-MM-DD":
            try:
                dt.date.fromisoformat(s[:10])
            except ValueError:
                issues.append(
                    f"{rel_path}: created_at '{s}' is not a valid ISO-8601 date (YYYY-MM-DD)."
                )

    # list fields sanity (if present)
    array_fields = (
        "legal_refs",
        "related_articles",
        "related_actions",
        "tags",
        "eli",
        "lawref",
        "related_artifacts",
    )
    for key in array_fields:
        if key in fm and fm[key] is not None and not isinstance(fm[key], list):
            issues.append(f"{rel_path}: Field '{key}' must be a list, got {type(fm[key]).__name__}")

    # evidence-specific soft rule: should reference legal basis somehow
    if (
        category == "evidence"
        and "legal_refs" not in fm
        and "related_articles" not in fm
        and "lawref" not in fm
    ):
        issues.append(
            f"{rel_path}: Evidence should include 'legal_refs' or 'related_articles' or 'lawref'."
        )

    return issues


# ---- Public API ----


def render_artifacts(
    templates_dir: str | Path,
    plan_json: str | Path,
    out_dir: str | Path,
    strict: bool = True,
    include_internals: bool = False,
    quality_report: dict[str, Any] | None = None,
) -> RenderResult:
    """Render templates into compliance artifacts.

    Args:
        templates_dir: Path to templates directory.
        plan_json: Path to plan JSON file.
        out_dir: Output directory.
        strict: If True, fail on missing template variables and validation issues.
            Use strict=False only for local authoring/development.
        include_internals: If True, include internal files (_partials, etc.).
        quality_report: Reserved for future use (no effect in renderer).

    Returns:
        RenderResult with statistics and diagnostics.
    """
    config = RenderConfig(
        templates_dir=Path(templates_dir),
        plan_path=Path(plan_json),
        out_dir=Path(out_dir),
        strict=strict,
        include_internals=include_internals,
        quality_report=quality_report,
    )
    renderer = ArtifactRenderer(config)
    return renderer.render()
