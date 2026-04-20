# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Application service for assembling document artifacts.

Decouples content generation (templating, hydration) from storage (export).
Includes robust AST-based analysis of templates to detect user inputs.
Moved from domain to app layer to keep domain free of Jinja dependencies.
"""

import logging
from pathlib import Path
from typing import Any, cast

from jinja2 import Environment, nodes, pass_context, select_autoescape
from jinja2.visitor import NodeVisitor

from src.common.jinja_env import create_jinja_env

logger = logging.getLogger(__name__)


@pass_context
def fill(ctx: dict[str, Any], key: str, default: str | None = None) -> str:
    """Jinja2 helper to inject user answers or placeholders.

    Usage in template: {{ fill("System Name") }}

    Uses [REQUIRED: X] format to make missing data obvious.

    Red Team Fix (Fase 0.1): Detect placeholder patterns in default values
    and replace them with user-friendly fallbacks to prevent [REQUIRED:] strings
    from appearing in production outputs.

    Args:
        ctx: Jinja2 context dictionary (automatically injected by @pass_context).
        key: Key to look up in answers dictionary.
        default: Optional default value if key not found.

    Returns:
        Answer value if found, sanitized default if provided, or "[REQUIRED: key]" placeholder.

    Example:
        >>> ctx = {"answers": {"System Name": "My AI System"}}
        >>> fill(ctx, "System Name")
        'My AI System'
        >>> fill(ctx, "Missing Key", "Default Value")
        'Default Value'
        >>> fill(ctx, "Missing Key")
        '[REQUIRED: Missing Key]'
    """
    # Prefer 'answers', fall back to 'wizard_answers' if provided.
    # Bug fix: Use explicit None check instead of truthiness to handle empty dicts correctly.
    # An empty dict {} should be considered "present" (no answers), not trigger fallback.
    answers_from_ctx = ctx.get("answers")
    answers = (
        answers_from_ctx if answers_from_ctx is not None else (ctx.get("wizard_answers") or {})
    )

    # Return answer if exists and is not empty
    if answers.get(key):
        return str(answers[key])

    # Red Team Fix (Fase 0.1): Check if default is itself a placeholder pattern
    # and sanitize it to prevent [REQUIRED:] strings in output
    if default is not None:
        # Detect [REQUIRED:], [FILL:], [TODO:] patterns in the default value
        if isinstance(default, str) and (
            "[REQUIRED:" in default or "[FILL:" in default or "[TODO:" in default
        ):
            # Return a sanitized version without the placeholder pattern
            # Use the key as a readable placeholder instead
            return f"({key})"
        return default

    return f"[REQUIRED: {key}]"


class FillFinder(NodeVisitor):
    """Jinja2 AST visitor to find all calls to the 'fill' function."""

    def __init__(self) -> None:
        """Initialize the visitor with an empty placeholders set."""
        self.placeholders: set[str] = set()

    def visit_Call(self, node: nodes.Call) -> None:
        """Visit a function call node and extract 'fill()' placeholders.

        Args:
            node: Jinja2 AST Call node to inspect.
        """
        # Check if it's a call to a function named 'fill'
        if isinstance(node.node, nodes.Name) and node.node.name == "fill" and node.args:
            # Extract the first argument (the key)
            first_arg = node.args[0]
            if isinstance(first_arg, nodes.Const):
                self.placeholders.add(first_arg.value)
        # Continue traversal
        self.generic_visit(node)


def extract_fill_placeholders(env: Environment, template_source: str) -> list[str]:
    """Analyze a Jinja2 template source to find all 'fill()' keys using AST parsing.

    Robust against whitespace, quotes, and nesting.
    """
    try:
        ast = env.parse(template_source)
        finder = FillFinder()
        finder.visit(ast)
        return sorted(list(finder.placeholders))
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Failed to parse template AST: {e}")
        return []


class ArtifactAssembler:
    """Assembles final artifact content from templates and data.

    Provides a clean interface for rendering Jinja2 templates with proper
    context management and the 'fill' helper function.
    """

    def __init__(self, templates_dir: Path, strict: bool = True) -> None:
        """Initialize the assembler with template directory and strict mode.

        Args:
            templates_dir: Path to directory containing Jinja2 templates.
            strict: If True, use StrictUndefined (fail on missing variables).
                    If False, use SilentEmpty (render missing variables as empty).
        """
        self.templates_dir = templates_dir
        self.strict = strict
        self._jinja_env: Environment | None = None

    @property
    def env(self) -> Environment:
        """Lazy-load Jinja environment and inject the 'fill' global."""
        if self._jinja_env is None:
            self._jinja_env = create_jinja_env(
                str(self.templates_dir),
                strict=self.strict,
                autoescape=select_autoescape(enabled_extensions=("html", "xml")),
            )
            # Inject the fill function globally
            self._jinja_env.globals["fill"] = fill
        return self._jinja_env

    def render(self, template_name: str, context: dict[str, Any]) -> str:
        """Render a Jinja2 template with the given context.

        Args:
            template_name: Name of the template file (relative to templates_dir).
            context: Dictionary with template variables.

        Returns:
            Rendered template content as string.

        Raises:
            jinja2.TemplateNotFound: If template doesn't exist.
            jinja2.UndefinedError: If strict mode and undefined variable accessed.
        """
        tmpl = self.env.get_template(template_name)
        return cast(str, tmpl.render(**context))

    def assemble(
        self,
        template_name: str,
        context: dict[str, Any],
        answers: dict[str, str] | None = None,
    ) -> str:
        """Full assembly pipeline: Render only.

        Hydration is now handled natively by Jinja2 via the 'fill()' function.
        The 'answers' dict is merged into the context so 'fill()' can access it.

        Args:
            template_name: Name of the template file (relative to templates_dir).
            context: Base template context dictionary.
            answers: Optional answers dictionary to merge into context.
                Merged as both 'answers' and 'wizard_answers' keys.

        Returns:
            Fully rendered template content as string.

        Raises:
            jinja2.TemplateNotFound: If template doesn't exist.
            jinja2.UndefinedError: If strict mode and undefined variable accessed.
        """
        # Create a new context merging the base context and answers
        full_context = context.copy()
        if answers:
            # Inject as both 'answers' and 'wizard_answers' for template access.
            full_context["answers"] = answers
            full_context["wizard_answers"] = answers

        # Single-pass render
        return cast(str, self.render(template_name, full_context))
