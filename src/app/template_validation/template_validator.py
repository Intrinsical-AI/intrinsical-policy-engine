# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Template validation utilities for detecting undefined variables.

This module provides tools to validate Jinja2 templates against a known
context schema, detecting variables that may cause runtime errors.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from jinja2 import Environment, meta
from jinja2.exceptions import TemplateAssertionError

# Import known context keys from central definition
from src.app.config.template_context import KNOWN_CONTEXT_KEYS

# Alias for local usage
KNOWN_CONTEXT_VARS = KNOWN_CONTEXT_KEYS

# Variables that are typically loop-local or filter results
KNOWN_LOCAL_VARS = {
    "item",
    "loop",
    "action",
    "flag",
    "article",
    "ref",
    "entry",
    "row",
    "key",
    "value",
    "label",
    "target",
    "i",
    "n",
    "_",
    "_legal_notice_path",
    "_q_status",
}


def _is_evidence_templates_path(template_path: Path) -> bool:
    parts = template_path.parts
    return "evidence" in parts and "templates" in parts


@dataclass
class TemplateIssue:
    """A single issue found in a template."""

    template_path: str
    line: int | None
    variable: str
    issue_type: str  # "undefined", "possibly_undefined", "deprecated"
    message: str


@dataclass
class TemplateValidationResult:
    """Result of validating templates."""

    templates_checked: int = 0
    issues: list[TemplateIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """Return True when no issues were detected."""
        return not self.issues

    @property
    def errors(self) -> list[TemplateIssue]:
        """Return issues that are definitively undefined references."""
        return [i for i in self.issues if i.issue_type in ("undefined", "error")]

    @property
    def warnings(self) -> list[TemplateIssue]:
        """Return potential/soft issues (deprecated or possibly undefined)."""
        return [i for i in self.issues if i.issue_type not in ("undefined", "error")]


class TemplateValidator:
    """Validates Jinja2 templates against known context schema."""

    def __init__(
        self,
        known_vars: set[str] | None = None,
        local_vars: set[str] | None = None,
    ):
        """Configure which variables are considered safe at template render time."""
        self.known_vars = known_vars or KNOWN_CONTEXT_VARS
        self.local_vars = local_vars or KNOWN_LOCAL_VARS

    def validate_template(
        self,
        template_path: Path,
        env: Environment | None = None,
    ) -> list[TemplateIssue]:
        """Validate a single template file.

        Args:
            template_path: Path to the template file
            env: Optional Jinja2 environment for parsing

        Returns:
            List of issues found in the template
        """
        issues: list[TemplateIssue] = []

        try:
            content = template_path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as e:
            issues.append(
                TemplateIssue(
                    template_path=str(template_path),
                    line=None,
                    variable="",
                    issue_type="error",
                    message=f"Failed to read template: {e}",
                )
            )
            return issues

        # Front-matter integrity check for markdown-like templates with YAML headers.
        issues.extend(self._validate_front_matter(template_path, content))

        # Create a minimal environment if not provided
        if env is None:
            env = Environment()

        # Parse the template and extract undeclared variables
        try:
            ast = env.parse(content)
            undeclared = meta.find_undeclared_variables(ast)
        except (SyntaxError, TypeError, ValueError, TemplateAssertionError) as e:
            issues.append(
                TemplateIssue(
                    template_path=str(template_path),
                    line=None,
                    variable="",
                    issue_type="error",
                    message=f"Failed to parse template: {e}",
                )
            )
            return issues

        # Check each undeclared variable
        for var in undeclared:
            # Extract the root variable name (e.g., "plan" from "plan.flags")
            root_var = var.split(".")[0]

            # Skip known local variables (loop variables, etc.)
            if root_var in self.local_vars:
                continue

            # Check if it's a known context variable
            if root_var not in self.known_vars:
                # Try to find the line number
                line = self._find_variable_line(content, var)

                issues.append(
                    TemplateIssue(
                        template_path=str(template_path),
                        line=line,
                        variable=var,
                        issue_type="possibly_undefined",
                        message=(
                            f"Variable '{var}' may not be defined in context. "
                            f"Known top-level vars: {sorted(self.known_vars)[:10]}..."
                        ),
                    )
                )

        return issues

    def _validate_front_matter(self, template_path: Path, content: str) -> list[TemplateIssue]:
        """Validate YAML front-matter at the start of markdown templates.

        This check is intentionally conservative: if the file starts with a
        front-matter marker ("---") but parse_front_matter returns None,
        we flag it as an integrity error (missing closing marker, invalid YAML,
        or mixed markdown/YAML block).
        """

        issues: list[TemplateIssue] = []

        name = template_path.name
        if not (name.endswith(".md") or name.endswith(".md.j2")):
            return issues

        text = content
        if text.startswith("\ufeff"):
            text = text[1:]
        t = text.replace("\r\n", "\n")
        evidence_prefixes = ("prv.", "dep.", "model.", "all.", "dst.")
        require_frontmatter = _is_evidence_templates_path(template_path) and name.startswith(
            evidence_prefixes
        )
        if not t.startswith("---\n"):
            if require_frontmatter:
                # For --strict-templates, treat missing front-matter as warning
                # (structure issue but not blocking for template validation)
                issues.append(
                    TemplateIssue(
                        template_path=str(template_path),
                        line=1,
                        variable="front_matter",
                        issue_type="possibly_undefined",  # Warning, not error
                        message=(
                            "Missing YAML front-matter "
                            "(recommended for evidence template .md files)."
                        ),
                    )
                )
            return issues

        # Check for closing marker (structure validation)
        end_idx = t.find("\n---\n", 4)
        if end_idx == -1:
            if require_frontmatter:
                issues.append(
                    TemplateIssue(
                        template_path=str(template_path),
                        line=1,
                        variable="front_matter",
                        issue_type="error",
                        message="Missing closing '---' marker in YAML front-matter.",
                    )
                )
            return issues

        # Extract front-matter block to check for Jinja2 syntax
        fm_block = t[4:end_idx]
        has_jinja = "{{" in fm_block or "{%" in fm_block

        # For templates with Jinja2, skip YAML parsing (expected in templates)
        # Only validate structure (opening/closing markers)
        if has_jinja:
            return issues

        # Delegate to common front-matter parser for non-Jinja templates
        from src.common.text.front_matter import parse_front_matter

        fm = parse_front_matter(content)
        if fm is None:
            if require_frontmatter:
                issues.append(
                    TemplateIssue(
                        template_path=str(template_path),
                        line=1,
                        variable="front_matter",
                        issue_type="error",
                        message=(
                            "Invalid YAML front-matter (parse error). "
                            "Note: Templates with Jinja2 placeholders skip YAML parsing."
                        ),
                    )
                )
            return issues

        return issues

    def _find_variable_line(self, content: str, variable: str) -> int | None:
        """Try to find the line number where a variable is used."""
        pattern = re.compile(rf"\b{re.escape(variable)}\b")
        for i, line in enumerate(content.split("\n"), 1):
            if pattern.search(line):
                return i
        return None

    def validate_directory(
        self,
        templates_dir: Path,
        extensions: set[str] | None = None,
    ) -> TemplateValidationResult:
        """Validate all templates in a directory.

        Args:
            templates_dir: Directory containing templates
            extensions: File extensions to check (default: .j2, .md, .yml, .yaml)

        Returns:
            TemplateValidationResult with all issues found
        """
        if extensions is None:
            extensions = {".j2", ".md", ".yml", ".yaml"}

        result = TemplateValidationResult()

        # Create environment for parsing with custom filters
        # Use a minimal environment but register custom filters for validation
        from src.common.jinja_env import add_days, resolve_relative_date, to_bool

        env = Environment()
        env.filters["add_days"] = add_days
        env.filters["bool"] = to_bool
        env.filters["resolve_date"] = resolve_relative_date
        env.tests["match"] = lambda value, pattern: bool(re.search(pattern, str(value)))

        for path in templates_dir.rglob("*"):
            if not path.is_file():
                continue

            if path.suffix.lower() not in extensions:
                continue

            # Skip non-Jinja files (check for {{ or {% markers)
            try:
                content = path.read_text(encoding="utf-8")
                has_jinja = "{{" in content or "{%" in content
                if not has_jinja and path.suffix.lower() != ".md":
                    continue
            except (OSError, UnicodeDecodeError):
                continue

            result.templates_checked += 1
            issues = self.validate_template(path, env)
            result.issues.extend(issues)

        return result


def validate_templates(
    templates_dir: str | Path,
    strict: bool = False,
) -> TemplateValidationResult:
    """Validate all templates in a directory.

    Args:
        templates_dir: Path to templates directory
        strict: If True, treat warnings as errors

    Returns:
        TemplateValidationResult with validation status
    """
    validator = TemplateValidator()
    result = validator.validate_directory(Path(templates_dir))

    if strict:
        # Convert warnings to errors in strict mode
        for issue in result.issues:
            if issue.issue_type == "possibly_undefined":
                issue.issue_type = "undefined"

    return result
