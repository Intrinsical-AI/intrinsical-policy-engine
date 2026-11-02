# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""One source-aware compilation path for lint, loading and evaluation."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ConfigDict, JsonValue, TypeAdapter, ValidationError

from intrinsical_policy_engine.adapters.contracts.yaml.template_validation import (
    validate_pack_templates,
)
from intrinsical_policy_engine.adapters.frameworks.layout_loader import (
    contained_path,
    load_framework_layout,
)
from intrinsical_policy_engine.common.document_io import parse_document, safe_yaml_error
from intrinsical_policy_engine.common.package_integrity import read_regular
from intrinsical_policy_engine.domain.bundles.models import (
    BacklogConfig,
    BundleNode,
    BundleProfile,
    BundleProfilesContract,
)
from intrinsical_policy_engine.domain.contract_models import (
    ActionsContract,
    ArticlesContract,
    CalendarContract,
    ContractBundle,
    DueRulesContract,
    FlagsContract,
    FrameworkPresentation,
    FrameworkSemantics,
    QuestionnaireContract,
    RulesContract,
    RuntimePolicies,
)
from intrinsical_policy_engine.domain.exceptions import ContractValidationError
from intrinsical_policy_engine.domain.framework_layout import FrameworkLayout
from intrinsical_policy_engine.domain.services.contract_validation import Problem, validate_bundle


@dataclass(frozen=True)
class CompiledPack:
    layout: FrameworkLayout
    bundle: ContractBundle
    bundle_profiles: dict[str, BundleProfile]
    backlog_config: BacklogConfig
    context_defaults: dict
    problems: tuple[Problem, ...] = ()


class CompilationError(ContractValidationError):
    def __init__(self, problems: list[Problem]):
        self.diagnostics = tuple(problems)
        super().__init__("pack compilation failed", problems=[p.to_dict() for p in problems])


class YamlContractsAdapter:
    def compile(self, path: str) -> CompiledPack:
        root = Path(path).resolve()
        try:
            layout = load_framework_layout(root)
        except (OSError, ValueError, yaml.YAMLError) as exc:
            manifest_problems = self._exception_problems(exc, "manifest.yml")
            raise CompilationError(manifest_problems) from exc
        problems: list[Problem] = []

        def merged(files: tuple[Path, ...], model: type[BaseModel] | None) -> dict:
            result: dict = {}
            for file in files:
                relative = file.relative_to(root).as_posix()
                try:
                    raw = parse_document(layout.source_bytes[relative].decode("utf-8"), ".yml")
                    if not isinstance(raw, dict):
                        raise ValueError("document root must be a mapping")
                    if model is not None:
                        model.model_validate(raw)
                    self._merge(result, raw)
                except (ValueError, yaml.YAMLError) as exc:
                    problems.extend(self._exception_problems(exc, relative))
            return result

        section_models: dict[str, type[BaseModel]] = {
            "flags": FlagsContract,
            "actions": ActionsContract,
            "rules": RulesContract,
            "articles": ArticlesContract,
            "calendar": CalendarContract,
            "due_rules": DueRulesContract,
            "questions": QuestionnaireContract,
        }
        sections = {
            key: merged(layout.resolve_contract_files(key), model)
            for key, model in section_models.items()
        }
        evidence_data = merged(layout.resolve_contract_files("evidence_map"), None)
        runtime_sections = {
            "semantics": merged(layout.resolve_runtime_files("semantics"), FrameworkSemantics),
            "policies": merged(layout.resolve_runtime_files("policies"), RuntimePolicies),
            "presentation": merged(
                layout.resolve_runtime_files("presentation"), FrameworkPresentation
            ),
        }
        profiles_data = merged(layout.bundle_profile_files, BundleProfilesContract)
        backlog_relative = layout.backlog_config_path.relative_to(root).as_posix()
        context_relative = layout.context_defaults_path.relative_to(root).as_posix()
        try:
            backlog = BacklogConfig.model_validate(
                parse_document(layout.source_bytes[backlog_relative].decode("utf-8"), ".yml")
            )
        except (ValueError, yaml.YAMLError) as exc:
            problems.extend(self._exception_problems(exc, backlog_relative))
            backlog = None
        try:
            context = parse_document(layout.source_bytes[context_relative].decode("utf-8"), ".yml")
            context = TypeAdapter(
                dict[str, JsonValue], config=ConfigDict(allow_inf_nan=False)
            ).validate_python(context, strict=True)
        except (ValueError, yaml.YAMLError) as exc:
            problems.extend(self._exception_problems(exc, context_relative))
            context = None
        if problems:
            raise CompilationError(problems)
        try:
            bundle = ContractBundle.model_validate(
                {
                    **sections,
                    "runtime": runtime_sections,
                    "evidence_map": evidence_data,
                    "path": str(root),
                    "manifest": layout.manifest,
                }
            )
            profiles_contract = BundleProfilesContract.model_validate(
                profiles_data or {"profiles": {}}
            )
        except ValidationError as exc:
            raise CompilationError(self._exception_problems(exc, "pack")) from exc
        profiles = profiles_contract.profiles
        for problem in validate_bundle(bundle, profiles):
            files = self._problem_sources(problem.field_path, layout)
            problems.append(
                Problem(
                    problem.code,
                    problem.severity,
                    files,
                    problem.msg,
                    field_path=problem.field_path,
                )
            )
        problems.extend(self._path_problems(layout, profiles, backlog))
        if context is not None:
            problems.extend(
                validate_pack_templates(
                    layout.source_bytes,
                    layout.manifest.templates_dir,
                    set(context),
                    {
                        node.template
                        for profile in profiles.values()
                        for node in self._all_nodes(profile.nodes)
                        if node.template is not None
                    },
                )
            )
        if any(problem.severity == "error" for problem in problems):
            raise CompilationError(problems)
        if backlog is None or context is None:
            raise RuntimeError("compilation invariant violated")
        return CompiledPack(layout, bundle, profiles, backlog, context, tuple(problems))

    def validate_detailed(self, path: str) -> list[Problem]:
        try:
            return list(self.compile(path).problems)
        except CompilationError as exc:
            return list(exc.diagnostics)

    @staticmethod
    def _problem_sources(where: str, layout: FrameworkLayout) -> str:
        section = where.split(".", 1)[0]
        if section == "runtime":
            name = where.split(".")[1]
            files = layout.resolve_runtime_files(name)
        elif section == "backlog":
            files = (layout.backlog_config_path,)
        elif section in {"bundle_profiles", "nodes", "profiles"}:
            files = layout.bundle_profile_files
        else:
            files = layout.resolve_contract_files(section)
        return (
            ",".join(file.relative_to(layout.framework_dir).as_posix() for file in files)
            or "manifest.yml"
        )

    @staticmethod
    def _exception_problems(exc: Exception, source: str) -> list[Problem]:
        if isinstance(exc, yaml.YAMLError):
            return [Problem("CONTRACT-SOURCE", "error", source, safe_yaml_error(exc))]
        if isinstance(exc, ValidationError):
            return [
                Problem(
                    "CONTRACT-TYPE",
                    "error",
                    source,
                    error["msg"],
                    field_path=".".join(str(part) for part in error["loc"]),
                )
                for error in exc.errors(
                    include_url=False, include_input=False, include_context=False
                )
            ]
        return [Problem("CONTRACT-SOURCE", "error", source, str(exc))]

    @classmethod
    def _merge(cls, target: dict, incoming: dict) -> None:
        for key, value in incoming.items():
            if key not in target:
                target[key] = value
            elif isinstance(target[key], list) and isinstance(value, list):
                target[key] = [*target[key], *value]
            elif isinstance(target[key], dict) and isinstance(value, dict):
                cls._merge(target[key], value)
            else:
                raise ValueError(f"multiple files define scalar field: {key}")

    @staticmethod
    def _path_problem(
        layout: FrameworkLayout,
        relative: str,
        where: str,
        *,
        base: Path | None = None,
        capture: bool = False,
        exists: bool = False,
        allow_root: bool = False,
    ) -> list[Problem]:
        try:
            path = contained_path(base or layout.framework_dir, relative, allow_root=allow_root)
            pack_relative = path.relative_to(layout.framework_dir).as_posix()
            if capture and pack_relative not in layout.source_bytes:
                layout.source_bytes[pack_relative] = read_regular(path)
            if exists and pack_relative not in layout.source_bytes:
                raise FileNotFoundError(f"referenced pack file does not exist: {relative}")
        except (ValueError, OSError) as exc:
            return [
                Problem(
                    "CONTRACT-PATH",
                    "error",
                    YamlContractsAdapter._problem_sources(where, layout),
                    str(exc),
                    field_path=where,
                )
            ]
        return []

    @classmethod
    def _node_path_problems(
        cls, layout: FrameworkLayout, nodes: tuple[BundleNode, ...], output_root: Path
    ) -> list[Problem]:
        problems: list[Problem] = []
        for node in nodes:
            for field, value in (("name", node.name), ("target", node.target)):
                if value is not None:
                    problems.extend(
                        cls._path_problem(
                            layout, value, f"nodes.{node.id}.{field}", base=output_root
                        )
                    )
            if node.template is not None:
                problems.extend(
                    cls._path_problem(
                        layout,
                        node.template,
                        f"nodes.{node.id}.template",
                        base=layout.templates_dir,
                        exists=True,
                    )
                )
            if node.source is not None:
                problems.extend(
                    cls._path_problem(
                        layout, node.source, f"nodes.{node.id}.source", capture=True, exists=True
                    )
                )
            problems.extend(
                cls._node_path_problems(layout, node.children, output_root / (node.name or ""))
            )
        return problems

    @classmethod
    def _path_problems(
        cls,
        layout: FrameworkLayout,
        profiles: dict[str, BundleProfile],
        backlog: BacklogConfig | None,
    ) -> list[Problem]:
        problems: list[Problem] = []
        if backlog is not None:
            split_ids = {split.id for split in backlog.splits}
            for split in backlog.splits:
                problems.extend(
                    cls._path_problem(layout, split.filename, f"backlog.splits.{split.id}.filename")
                )
            required = {"engineering_backlog": "engineering", "legal_backlog": "governance"}
            for profile in profiles.values():
                for node in cls._all_nodes(profile.nodes):
                    if node.context in required and required[node.context] not in split_ids:
                        problems.append(
                            Problem(
                                "BACKLOG-CONTEXT",
                                "error",
                                cls._problem_sources("nodes", layout),
                                f"context {node.context} requires split {required[node.context]}",
                                field_path=f"nodes.{node.id}.context",
                            )
                        )
        for profile in profiles.values():
            problems.extend(
                cls._path_problem(
                    layout, profile.root_dir, f"profiles.{profile.id}.root_dir", allow_root=True
                )
            )
            problems.extend(
                cls._node_path_problems(
                    layout, profile.nodes, layout.framework_dir / profile.root_dir
                )
            )
        return problems

    @staticmethod
    def _all_nodes(nodes: tuple[BundleNode, ...]):
        for node in nodes:
            yield node
            yield from YamlContractsAdapter._all_nodes(node.children)
