# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""The single artifact assembler with strict variables and explicit answers."""

from pathlib import Path
from typing import Any

from jinja2 import BaseLoader, Environment, TemplateNotFound, pass_context

from intrinsical_policy_engine.common.jinja_env import create_jinja_env


@pass_context
def fill(ctx: dict[str, Any], key: str, default: str | None = None) -> str:
    value = ctx.get("answers", {}).get(key)
    if value is not None and value != "":
        return str(value)
    if default is not None:
        return default
    raise ValueError(f"Missing required template answer: {key}")


class SnapshotTemplateLoader(BaseLoader):
    def __init__(self, sources: dict[str, bytes]) -> None:
        self.sources = sources

    def get_source(self, environment: Environment, template: str):
        if template not in self.sources:
            raise TemplateNotFound(template)
        return self.sources[template].decode("utf-8"), template, lambda: True


class ArtifactAssembler:
    def __init__(
        self, templates_dir: Path, *, template_sources: dict[str, bytes] | None = None
    ) -> None:
        self._env = create_jinja_env(str(templates_dir))
        if template_sources is not None:
            self._env.loader = SnapshotTemplateLoader(template_sources)
        self._env.globals["fill"] = fill

    @property
    def env(self) -> Environment:
        return self._env

    def render(self, template_name: str, context: dict[str, Any]) -> str:
        return self._env.get_template(template_name).render(**context)

    def assemble(
        self, template_name: str, context: dict[str, Any], answers: dict[str, Any] | None = None
    ) -> str:
        return self.render(
            template_name, {**context, **({"answers": answers} if answers is not None else {})}
        )
