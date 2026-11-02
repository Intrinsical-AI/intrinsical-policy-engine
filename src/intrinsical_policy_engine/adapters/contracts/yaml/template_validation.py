# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Compile template syntax and static references from captured pack bytes."""

from pathlib import PurePosixPath

from jinja2 import TemplateError, meta, nodes

from intrinsical_policy_engine.app.config.template_context import KNOWN_CONTEXT_KEYS
from intrinsical_policy_engine.app.rendering.templating import SnapshotTemplateLoader
from intrinsical_policy_engine.common.jinja_env import create_jinja_env
from intrinsical_policy_engine.domain.services.contract_validation import Problem


def _static_names(expression: nodes.Expr) -> list[str] | None:
    if isinstance(expression, nodes.Const) and isinstance(expression.value, str):
        return [expression.value]
    if isinstance(expression, (nodes.List, nodes.Tuple)):
        names = [_static_names(item) for item in expression.items]
        if all(name is not None for name in names):
            return [item for name in names if name is not None for item in name]
    return None


def _references(ast: nodes.Template, sources: dict[str, bytes]):
    for reference in ast.find_all((nodes.Include, nodes.Extends, nodes.Import, nodes.FromImport)):
        if not isinstance(
            reference, (nodes.Include, nodes.Extends, nodes.Import, nodes.FromImport)
        ):
            continue
        names = _static_names(reference.template)
        if names is None:
            continue
        existing = [name for name in names if name in sources]
        optional = isinstance(reference, nodes.Include) and reference.ignore_missing
        if existing or not optional:
            yield names, existing


def validate_pack_templates(
    source_bytes: dict[str, bytes],
    prefix: str,
    context_keys: set[str],
    template_names: set[str],
) -> list[Problem]:
    problems: list[Problem] = []
    marker = prefix + "/"
    sources = {
        name.removeprefix(marker): data
        for name, data in source_bytes.items()
        if name.startswith(marker)
    }
    pending = set(template_names) | {
        name for name in sources if PurePosixPath(name).suffix == ".j2"
    }
    seen: set[str] = set()
    loader = SnapshotTemplateLoader(sources)
    env = create_jinja_env(loader)
    env.globals["fill"] = lambda *_args: ""
    known = KNOWN_CONTEXT_KEYS | context_keys
    while pending:
        name = min(pending)
        pending.remove(name)
        if name in seen:
            continue
        seen.add(name)
        source = marker + name
        try:
            content, _, _ = loader.get_source(env, name)
            ast = env.parse(content, name=name)
            roots = meta.find_undeclared_variables(ast)
        except (UnicodeDecodeError, TemplateError) as exc:
            problems.append(Problem("TPL_SYNTAX", "error", source, str(exc)))
            continue
        for root in sorted(roots - known):
            problems.append(
                Problem(
                    "TPL_UNDEFINED_ROOT",
                    "warning",
                    source,
                    f"Template root is not supplied by the pack: {root}",
                )
            )
        for names, existing in _references(ast, sources):
            if not existing:
                problems.append(
                    Problem(
                        "TPL_REFERENCE",
                        "error",
                        source,
                        f"Referenced template does not exist in the pack: {', '.join(names)}",
                        field_path="template_reference",
                    )
                )
            pending.update(existing)
    return problems
