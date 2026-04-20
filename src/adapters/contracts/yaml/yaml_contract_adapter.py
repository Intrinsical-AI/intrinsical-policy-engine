# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""YAML contracts adapter for loading and validating compliance contracts."""

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal

import yaml

from src.adapters.contracts.yaml.contract_validators import ContractBusinessValidator
from src.adapters.export.base.evidence.evidence_utils import (
    load_evidence_map_for_bundle,
    normalize_evidence_map,
)
from src.adapters.frameworks.layout_loader import (
    load_framework_layout_cached,
)
from src.adapters.frameworks.layout_loader import (
    resolve_manifest_entries as resolve_layout_entries,
)
from src.domain.bundles.models import BundleProfile
from src.domain.exceptions import (
    SchemaValidationError,
    StrictContractViolation,
    YAMLLoadError,
)
from src.domain.ports import ContractBundle, ContractsPort
from src.domain.services.rule_engine import DSLVersionError, validate_dsl_version

if TYPE_CHECKING:
    from jsonschema import Draft202012Validator

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Problem:
    """Structured representation of a validation or loading issue."""

    code: str
    severity: Literal["ERROR", "WARN"]
    where: str
    msg: str


class YamlContractsAdapter(ContractsPort):
    """Adapter for loading YAML-based contract bundles.

    Loads and validates compliance contracts from YAML files in a bundle directory.
    Supports strict and tolerant validation modes, with comprehensive error reporting.

    Strict Mode:
        By default, strict mode is enabled in production (IPE_ENV != 'dev').
        In strict mode, validation errors cause immediate failure.
        Set IPE_STRICT_CONTRACTS=0 to disable strict mode explicitly.
    """

    def __init__(self, *, strict: bool | None = None) -> None:
        """Configure validator caches and strict/tolerant behavior.

        Initializes JSON Schema validator cache and business validator.
        Strict mode is determined from environment variables if not explicitly provided.

        Args:
            strict: Explicit strict flag. If None, the value is derived from env vars:
                - IPE_STRICT_CONTRACTS: Explicit override
                  ('0'/'false'/'no' = False, '1'/'true'/'yes' = True)
                - IPE_ENV: 'dev' = tolerant, otherwise strict (default)
        """
        # Cache for compiled JSON Schema validators by absolute schema path
        self._schema_cache: dict[str, Draft202012Validator] = {}

        # Business validator for semantic rules
        self._business_validator = ContractBusinessValidator()

        # Strict mode: enabled by default unless in dev environment.
        self._strict = self._resolve_strict_mode(strict)

    @staticmethod
    def _resolve_strict_mode(strict: bool | None) -> bool:
        """Resolve strictness with explicit argument taking priority over env vars."""
        if strict is not None:
            return strict

        env = (os.getenv("IPE_ENV") or os.getenv("LEXOPS_ENV") or "prod").lower()
        strict_env = (
            (os.getenv("IPE_STRICT_CONTRACTS") or os.getenv("LEXOPS_STRICT_CONTRACTS") or "")
            .strip()
            .lower()
        )

        if strict_env in ("0", "false", "no"):
            return False
        if strict_env in ("1", "true", "yes"):
            return True

        # Default: strict in production, tolerant in dev.
        return env != "dev"

    @staticmethod
    def _should_tolerate_questions_errors() -> bool:
        """Check if questions.yml errors should be tolerated."""
        tolerate = (
            os.getenv("IPE_TOLERATE_QUESTIONS_ERRORS")
            or os.getenv(
                "LEXOPS_TOLERATE_QUESTIONS_ERRORS",
            )
            or ""
        )
        return tolerate.lower() in (
            "1",
            "true",
            "yes",
        )

    @staticmethod
    def _profiles_by_kind(profiles: dict[str, BundleProfile]) -> dict[str, BundleProfile]:
        """Index bundle profiles by their declared kind."""
        return {
            profile.kind: profile
            for profile in profiles.values()
            if isinstance(profile, BundleProfile)
        }

    @staticmethod
    def _profile_for_role(
        profiles_by_kind: dict[str, BundleProfile], role: str | None
    ) -> BundleProfile | None:
        """Resolve a profile by role using the exact role/kind convention.

        The coverage-rule convention is intentionally strict: `rule.role` must match
        `BundleProfile.kind` exactly. If it does not, the rule is skipped.
        """
        if not role:
            return None
        return profiles_by_kind.get(str(role))

    def load(self, path: str) -> ContractBundle:  # noqa: C901
        """Load and validate contract bundle from directory.

        Single I/O path: load YAML once, normalize once, validate without re-reading disk.
        Performs comprehensive validation including schema validation, business rules,
        and evidence map validation.

        Args:
            path: Path to bundle directory containing YAML contract files.

        Returns:
            ContractBundle with loaded and validated contracts.

        Raises:
            YAMLLoadError: If critical YAML files cannot be loaded.
            SchemaValidationError: If contracts fail schema validation (in strict mode).
            StrictContractViolation: If business rules are violated (in strict mode).

        Note:
            Evidence map validation (INV-B2) is enforced in strict mode:
            - Invalid evidence_map keys (not in Articles/Actions) cause hard failure
            - Missing template files generate warnings (not errors)
        """
        base_path = Path(path)

        # Load YAML files and collect loading errors
        loading_problems: list[str] = []
        contracts = self._load_yaml_files(base_path, loading_problems)

        if any(msg.startswith("Error loading manifest.yml:") for msg in loading_problems):
            raise YAMLLoadError(
                "Contract loading requires manifest.yml and canonical manifest-declared files",
                yaml_error="\n".join(loading_problems),
            )

        # Validate in-memory contracts (diagnostics do not re-read disk).
        # Strict validation is explicit; no "official bundle" shortcuts.
        # load() and lint() behave the same under --strict.
        # Reference: docs/invariants/ENGINE-ARCHITECTURE-v1.md.
        use_framework_schemas = True
        strict_schemas = self._strict

        # Load evidence map early for validation
        raw_evidence_map = contracts.get("evidence_map")
        if isinstance(raw_evidence_map, dict):
            evidence_map = normalize_evidence_map(raw_evidence_map) or {}
        else:
            evidence_map = load_evidence_map_for_bundle(str(base_path))

        val_errors, _val_problems = self._validate_contracts(
            contracts,
            base_path,
            use_framework_schemas=use_framework_schemas,
            strict_schemas=strict_schemas,
            evidence_map=evidence_map,
        )

        # Fail-fast policy in Load():
        #  - YAML parse errors in critical files cause immediate failure
        #  - questions.yml errors are critical unless explicitly tolerated
        tolerate_questions = self._should_tolerate_questions_errors()
        critical_loading_errors: list[str] = []
        for msg in loading_problems:
            if msg.startswith("Error loading "):
                if "questions.yml" in msg and tolerate_questions:
                    continue
                critical_loading_errors.append(msg)
        if critical_loading_errors:
            raise YAMLLoadError(
                f"Critical YAML loading errors: {len(critical_loading_errors)} file(s) failed",
                yaml_error="\n".join(critical_loading_errors),
            )

        filtered_errors: list[str] = []
        evidence_warnings: list[str] = []  # Collect evidence errors separately
        tolerate_questions = self._should_tolerate_questions_errors()

        for e in loading_problems + val_errors:
            # Be tolerant to questions.yml YAML/IO errors ONLY if explicitly enabled
            if tolerate_questions:
                if e.startswith("Error loading questions.yml:") or (
                    ("questions.yml" in e) and ("Error loading" in e)
                ):
                    continue
                # Tolerate type normalization warning for questions
                if e.startswith("questions must be an object;"):
                    continue

            # Evidence KEY errors are FATAL in strict mode.
            # INV-B2 (No phantom evidences) requires that invalid evidence_map keys
            # (keys not found in Articles or Actions) cause a hard failure when --strict.
            # However, "template not found" errors are WARNINGS in all modes because
            # they indicate missing template files, not data integrity issues.
            # The framework may define evidence paths for templates not yet created.
            if e.startswith("[EVIDENCE]"):
                # Distinguish between key errors (fatal) and template errors (warning)
                is_key_error = "not found in Articles or Actions" in e
                if self._strict and is_key_error:
                    filtered_errors.append(e)  # Fatal: INV-B2 violation
                else:
                    evidence_warnings.append(e)  # Warning: missing template
                continue

            filtered_errors.append(e)

        # Log evidence warnings (always - for visibility)
        if evidence_warnings:
            logger.warning(
                "contracts.load.evidence_warnings",
                extra={
                    "path": str(base_path),
                    "warning_count": len(evidence_warnings),
                    "warnings": evidence_warnings[:10],  # Limit to first 10 for brevity
                },
            )

        if filtered_errors:
            if self._strict:
                # In strict mode, fail fast on any validation error
                raise StrictContractViolation(
                    "Contract validation failed in strict mode",
                    error_count=len(filtered_errors),
                    critical_errors=filtered_errors,
                )
            else:
                # In tolerant mode (dev), log but do not fail to preserve DX
                logger.warning(
                    "contracts.load.tolerant_errors",
                    extra={
                        "path": str(base_path),
                        "error_count": len(filtered_errors),
                        "errors": filtered_errors,
                    },
                )
                pass

        # Read optional bundle meta
        meta_version = None
        meta_path = base_path / "bundle.yml"
        if meta_path.exists():
            try:
                _meta = yaml.safe_load(meta_path.read_text(encoding="utf-8")) or {}
                if isinstance(_meta, dict) and isinstance(_meta.get("version"), str):
                    meta_version = _meta.get("version")
            except (yaml.YAMLError, OSError, UnicodeDecodeError):
                meta_version = None

        # Parse YAML dicts into typed Pydantic models
        # All validation happens here at load time (fail-fast)
        bundle = ContractBundle.from_yaml_dicts(
            flags=contracts.get("flags") or {},
            actions=contracts.get("actions") or {},
            rules=contracts.get("rules") or {},
            articles=contracts.get("articles") or {},
            due_rules=contracts.get("due_rules") or {"rules": []},
            dedups=contracts.get("dedups") or {"mappings": []},
            calendar=contracts.get("calendar") or {},
            questions=contracts.get("questions") or {},
            risk_config=contracts.get("risk_config") or {},
            runtime=contracts.get("runtime") or {},
            version=(meta_version or (contracts.get("actions") or {}).get("version", "1.0.0")),
            path=str(base_path),
            evidence_map=evidence_map,
            audit=contracts.get("audit") or {},
            metadata=contracts.get("metadata") or {},
        )

        return bundle

    def _load_coverage_rules(self, base_path: Path) -> list[dict]:
        """Load coverage rules from the framework delivery policy area."""
        try:
            layout = load_framework_layout_cached(base_path)
        except (FileNotFoundError, ValueError):
            return []
        rules_path = layout.framework_dir / "delivery" / "policy" / "coverage_rules.yml"
        if not rules_path.exists():
            rules_path = layout.framework_dir / "bundle" / "coverage_rules.yml"
        if not rules_path.exists():
            return []

        try:
            data = yaml.safe_load(rules_path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "rules" in data:
                from typing import cast

                return cast("list[dict]", data["rules"])
            return []
        except (yaml.YAMLError, OSError) as e:
            logger.warning(f"Error loading coverage_rules.yml: {e}")
            return []

    def _expand_coverage_rules(
        self,
        rules: list[dict],
        profiles: dict[str, BundleProfile],
        evidence_map: dict[str, list[dict]],
    ) -> None:
        """Expand coverage rules into BundleNodes and merge into profiles.

        This implements the Generative Strategy (Contract v2):
        Rule + EvidenceMap -> BundleNodes
        """
        from src.domain.bundles.models import BundleNode
        from src.domain.coverage.context import canonical_node_id

        # Coverage rules are mapped by the exact role/kind convention.
        profiles_by_kind = self._profiles_by_kind(profiles)

        for rule in rules:
            role = rule.get("role")
            article = rule.get("article")
            kind = rule.get("kind")
            applies_if = rule.get("applies_if")

            if not (role and article):
                continue

            target_profile = self._profile_for_role(profiles_by_kind, role)
            if not target_profile:
                logger.debug(
                    "contracts.load.bundle_profiles.role_kind_mismatch",
                    extra={
                        "role": role,
                        "known_kinds": sorted(profiles_by_kind),
                        "article": article,
                    },
                )
                # If no profile exists for this role, we skip for now
                # (or could create one, but that requires more config like root_dir)
                continue

            # Resolve evidences from evidence_map
            # evidence_map keys might be TOPIC-XX or just XX
            # We try exact match first
            evidences = evidence_map.get(article)
            if not evidences and article.startswith("TOPIC-"):
                # Try without prefix
                evidences = evidence_map.get(article.replace("TOPIC-", ""))

            if not evidences:
                # No evidences defined for this article.
                # For critical rules, we might want a placeholder.
                # Currently: skip
                continue

            # Generate Node
            # We use a simplified strategy: 1 Rule -> 1 Parent Node (Folder or File)
            node_id = canonical_node_id(role, article)

            # Determine template path
            # Strategy: look for override, else generic
            # For now, hardcode generic logic based on 'kind'
            # kind: "memo" -> generic memo
            # kind: "folder" -> generic folder

            # For this prototype, we'll construct the node but leave template generic
            # logic for the next iteration. We map to artifacts/bundles/generics/{kind}.
            template_path = f"artifacts/bundles/generics/{kind}/README.md.j2"

            # Construct predicates
            predicates = [applies_if] if applies_if and applies_if != "true" else []

            # Create Nodes
            # Fix 2025-12-27: BundleExporter doesn't render templates for dirs.
            # We must split "folder" kind into (Dir + README File).

            if kind in ("folder", "dir"):
                # 1. Parent Directory
                node = BundleNode(
                    id=node_id,
                    kind="dir",
                    name=f"{article}_View",
                    template=None,
                    source=None,
                    target=None,
                    context=article,
                    predicates=predicates,
                    children=[],
                    # Trace back linked to folder for coverage counting
                    trace_back_to={
                        "actions": [],
                        "evidences": [article],
                        "critical": rule.get("priority") == "critical",
                    },
                )

                # 2. Child index
                readme = BundleNode(
                    id=f"{node_id}_readme",
                    kind="file",
                    name="00_INDEX.md",
                    template=template_path,
                    source=None,
                    target=None,
                    context=article,
                    predicates=predicates,
                    trace_back_to=None,
                )
                node.children.append(readme)

            else:
                # File-based kind (e.g. memo)
                node = BundleNode(
                    id=node_id,
                    kind="file",
                    name=f"{article}_View.md",
                    template=template_path,
                    source=None,
                    target=None,
                    context=article,
                    predicates=predicates,
                    trace_back_to={
                        "actions": [],
                        "evidences": [article],
                        "critical": rule.get("priority") == "critical",
                    },
                )

            # Add to profile
            target_profile.nodes.append(node)

    def load_bundle_profiles(self, path: str) -> dict[str, BundleProfile]:  # noqa: C901
        """Load declarative bundle profiles from the canonical framework layout."""
        base_path = Path(path)
        try:
            layout = load_framework_layout_cached(base_path)
        except (FileNotFoundError, ValueError) as exc:
            raise YAMLLoadError(
                "Bundle profiles require a canonical framework manifest layout"
            ) from exc
        profiles: dict[str, BundleProfile] = {}
        problems: list[str] = []

        def load_single_yaml(path: Path, context: str) -> dict | None:
            if not path.exists():
                problems.append(f"[BUNDLE][ERROR] Error loading {context}: FileNotFoundError")
                return None
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return data
                problems.append(
                    f"[BUNDLE][ERROR] Error loading {context}: Expected mapping at root"
                )
                return None
            except (yaml.YAMLError, OSError, UnicodeDecodeError) as e:
                problems.append(f"[BUNDLE][ERROR] Error loading {context}: {e}")
                return None

        def merge_profiles(src_profiles: dict) -> None:
            for pid, pdata in src_profiles.items():
                try:
                    if isinstance(pdata, dict):
                        pdata = pdata.copy()
                        if "id" not in pdata:
                            pdata["id"] = pid
                    profile = BundleProfile(**pdata)
                    if profile.id != pid:
                        problems.append(
                            f"[BUNDLE][WARN] Profile key '{pid}' does not match id '{profile.id}'"
                        )
                    profiles[profile.id] = profile
                except (ValueError, TypeError) as e:
                    problems.append(f"[BUNDLE][ERROR] Failed to parse profile '{pid}': {e}")

        def load_manifest_evidence_map() -> dict[str, list[dict]] | None:
            merged: dict[str, list[dict]] = {}
            for f in layout.resolve_contract_files("evidence_map"):
                data = load_single_yaml(f, f.name)
                if not isinstance(data, dict):
                    continue
                for key, value in data.items():
                    if isinstance(value, list):
                        merged.setdefault(str(key), []).extend(value)
            normalized = normalize_evidence_map(merged)
            return normalized or None

        bundle_files = layout.resolve_bundle_profile_files()

        for f in bundle_files:
            if f.name == "schema.yml":
                continue
            data = load_single_yaml(f, f.name)
            if not isinstance(data, dict):
                continue
            if "profiles" in data and isinstance(data["profiles"], dict):
                merge_profiles(data["profiles"])

        # Generator Strategy (Fase 3): Expand coverage rules
        coverage_rules = self._load_coverage_rules(base_path)
        if coverage_rules:
            evidence_map = load_manifest_evidence_map() or load_evidence_map_for_bundle(
                str(base_path)
            )
            self._expand_coverage_rules(coverage_rules, profiles, evidence_map)

        if problems:
            if self._strict:
                raise YAMLLoadError(
                    f"Bundle profiles validation failed: {len(problems)} errors",
                    yaml_error="\n".join(problems),
                )
            logger.warning("contracts.load.bundle_errors", extra={"errors": problems})

        return profiles

    def validate(
        self, path: str, use_framework_schemas: bool = False, strict_schemas: bool = False
    ) -> list[str]:
        """Validate contract bundle and return list[str] problems.

        Single I/O path: load YAML once and validate without normalizing or re-reading disk.
        """
        base_path = Path(path)
        loading_problems: list[str] = []
        contracts = self._load_yaml_files(base_path, loading_problems)

        # Load evidence map for validation
        raw_evidence_map = contracts.get("evidence_map")
        if isinstance(raw_evidence_map, dict):
            evidence_map = normalize_evidence_map(raw_evidence_map) or {}
        else:
            evidence_map = load_evidence_map_for_bundle(str(base_path))

        val_errors, _val_problems = self._validate_contracts(
            contracts,
            base_path,
            use_framework_schemas=use_framework_schemas,
            strict_schemas=strict_schemas,
            evidence_map=evidence_map,
        )
        return loading_problems + val_errors

    def validate_detailed(
        self, path: str, use_framework_schemas: bool = False, strict_schemas: bool = False
    ) -> tuple[list[str], list[Problem]]:
        """Validate and return (textual_problems, structured_problems).

        - textual_problems: list[str] combining loading and validation messages (legacy format)
        - structured_problems: list[Problem] collected during validation (e.g., deprecations)
        """
        base_path = Path(path)
        loading_problems: list[str] = []
        contracts = self._load_yaml_files(base_path, loading_problems)

        # Load evidence map for validation
        raw_evidence_map = contracts.get("evidence_map")
        if isinstance(raw_evidence_map, dict):
            evidence_map = normalize_evidence_map(raw_evidence_map) or {}
        else:
            evidence_map = load_evidence_map_for_bundle(str(base_path))

        val_errors, val_problems = self._validate_contracts(
            contracts,
            base_path,
            use_framework_schemas=use_framework_schemas,
            strict_schemas=strict_schemas,
            evidence_map=evidence_map,
        )
        return (loading_problems + val_errors), val_problems

    def _validate_contracts(
        self,
        contracts: dict,
        base_path: Path,
        *,
        use_framework_schemas: bool,
        strict_schemas: bool,
        evidence_map: dict | None = None,
    ) -> tuple[list[str], list[Problem]]:
        """Run schema and business validation for a loaded contract bundle.

        Args:
            contracts: Full contract payload keyed by section (flags, actions, etc.).
            base_path: Bundle directory for schema resolution.
            use_framework_schemas: Whether to fallback to bundled framework schemas.
            strict_schemas: Whether schema loading/validation errors should raise.

        Returns:
            Tuple of flattened error strings and structured :class:`Problem` entries.
        """
        problems: list[str] = []
        internal: list[Problem] = []

        rules_data = contracts.get("rules")
        if isinstance(rules_data, dict):
            try:
                valid, err = validate_dsl_version(rules_data, strict=strict_schemas)
            except DSLVersionError as exc:
                problems.append(f"[DSL][ERROR] {exc}")
            else:
                if not valid and err:
                    prefix = "[DSL][ERROR]" if strict_schemas else "[DSL][WARN]"
                    problems.append(f"{prefix} {err}")

        # JSON Schema validation
        self._validate_schemas(
            base_path,
            contracts,
            problems,
            use_framework_schemas=use_framework_schemas,
            strict_schemas=strict_schemas,
        )

        # Business rule validation (delegated to dedicated validator)
        business_problems = self._business_validator.validate_all(
            contracts, base_path, evidence_map
        )
        problems.extend(business_problems)

        return problems, internal

    def _load_yaml_files(self, base_path: Path, problems: list[str]) -> dict:  # noqa: C901
        """Load YAML files (or directories of files) and collect loading errors."""

        def load_single_yaml(
            path: Path, name_context: str, *, optional: bool = False
        ) -> dict | None:
            # For optional files, silently return None if they don't exist
            if not path.exists():
                if not optional:
                    problems.append(
                        f"Error loading {name_context}: FileNotFoundError: file not found"
                    )
                return None

            try:
                # Use CSafeLoader if available for performance, fallback to SafeLoader
                try:
                    from yaml import CSafeLoader as Loader
                except ImportError:
                    from yaml import SafeLoader as Loader  # type: ignore

                loaded = yaml.load(path.read_text(encoding="utf-8"), Loader=Loader)
            except yaml.YAMLError as e:
                mark = getattr(e, "problem_mark", None)
                if mark is not None:
                    problems.append(
                        f"Error loading {name_context}: {getattr(e, 'problem', str(e))} "
                        f"at line {mark.line + 1}, column {mark.column + 1}"
                    )
                else:
                    problems.append(f"Error loading {name_context}: {e}")
                return None
            except (OSError, UnicodeDecodeError) as e:
                problems.append(f"Error loading {name_context}: {e.__class__.__name__}: {e}")
                return None

            # Only mappings are valid contract roots for this loader; ignore other YAML types
            return loaded if isinstance(loaded, dict) else None

        def merge_data(target: dict, source: dict, filename: str):
            """Smart merge for contract sections."""
            for key, value in source.items():
                if key not in target:
                    target[key] = value
                elif isinstance(target[key], list) and isinstance(value, list):
                    target[key].extend(value)
                elif isinstance(target[key], dict) and isinstance(value, dict):
                    # Shallow merge for top-level dicts (e.g. defaults)
                    target[key].update(value)
                elif target[key] != value:
                    # Conflict or scalar overwrite - usually acceptable for 'version' or 'schema'
                    # but worth noting if curious. For now, last write wins for scalars.
                    target[key] = value

        def _load_manifest_section(
            manifest_contracts: dict, section: str, *, required: bool = False
        ) -> dict | None:
            """Load a contract section using manifest-declared entries."""
            entries = manifest_contracts.get(section)
            if not entries:
                if required:
                    problems.append(f"Error loading manifest {section}: missing section")
                return None

            resolved, missing = resolve_layout_entries(base_path, entries)
            for missed in missing:
                problems.append(f"Error loading manifest {section}: FileNotFoundError: {missed}")

            merged: dict = {}
            for path in resolved:
                data = load_single_yaml(path, f"manifest/{section}/{path.name}")
                if data:
                    merge_data(merged, data, path.name)
            return merged if merged else None

        framework_version_path = base_path / "FRAMEWORK_VERSION.yml"
        manifest_path = base_path / "manifest.yml"

        framework_version = (
            load_single_yaml(
                framework_version_path,
                "FRAMEWORK_VERSION.yml",
                optional=True,
            )
            or {}
        )
        manifest_meta = (
            load_single_yaml(
                manifest_path,
                "manifest.yml",
                optional=True,
            )
            or {}
        )
        manifest_version = None
        if isinstance(manifest_meta, dict):
            manifest_version = manifest_meta.get("version")
        framework_version_value = None
        if isinstance(framework_version, dict):
            framework_block = framework_version.get("framework")
            if isinstance(framework_block, dict):
                framework_version_value = framework_block.get("version")
        if (
            isinstance(manifest_version, str)
            and isinstance(framework_version_value, str)
            and manifest_version != framework_version_value
        ):
            problems.append(
                "[VERSION][ERROR] manifest.yml version "
                f"'{manifest_version}' does not match FRAMEWORK_VERSION.yml "
                f"version '{framework_version_value}'"
            )
        if not manifest_path.exists():
            problems.append("Error loading manifest.yml: FileNotFoundError: file not found")

        metadata: dict = {}
        if isinstance(framework_version, dict):
            metadata["framework_version"] = framework_version
            framework_block = framework_version.get("framework")
            if isinstance(framework_block, dict):
                metadata["engine_compatibility"] = framework_block.get("engine_compatibility", {})
                metadata.setdefault("version", framework_block.get("version"))
                metadata["framework"] = framework_block
        if isinstance(manifest_meta, dict):
            metadata["manifest"] = manifest_meta
            if "compatible_engine_versions" in manifest_meta:
                engine_compat = metadata.setdefault("engine_compatibility", {})
                engine_compat.setdefault(
                    "compatible_engine_versions",
                    manifest_meta.get("compatible_engine_versions", []),
                )

        manifest_contracts = None
        if isinstance(manifest_meta, dict):
            contracts_block = manifest_meta.get("contracts")
            if isinstance(contracts_block, dict):
                manifest_contracts = contracts_block

        required_sections = {"flags", "rules", "actions", "articles"}
        required_runtime_sections = {"semantics", "policies", "presentation"}

        manifest_runtime = None
        if isinstance(manifest_meta, dict):
            runtime_block = manifest_meta.get("runtime")
            if isinstance(runtime_block, dict):
                manifest_runtime = runtime_block

        runtime_payload: dict[str, dict] = {}
        if manifest_runtime is not None:
            for section in sorted(required_runtime_sections):
                runtime_payload[section] = (
                    _load_manifest_section(
                        manifest_runtime,
                        section,
                        required=section in required_runtime_sections,
                    )
                    or {}
                )
        else:
            runtime_payload = {
                "semantics": {},
                "policies": {},
                "presentation": {},
            }

        if manifest_contracts is not None:
            contracts = {
                "flags": _load_manifest_section(
                    manifest_contracts, "flags", required="flags" in required_sections
                ),
                "actions": _load_manifest_section(
                    manifest_contracts, "actions", required="actions" in required_sections
                ),
                "rules": _load_manifest_section(
                    manifest_contracts, "rules", required="rules" in required_sections
                ),
                "evidence_map": _load_manifest_section(manifest_contracts, "evidence_map"),
                "dedups": _load_manifest_section(manifest_contracts, "dedups"),
                "due_rules": _load_manifest_section(manifest_contracts, "due_rules"),
                "calendar": _load_manifest_section(manifest_contracts, "calendar"),
                "audit": _load_manifest_section(manifest_contracts, "audit"),
                "risk_config": _load_manifest_section(manifest_contracts, "risk_config"),
                "articles": _load_manifest_section(
                    manifest_contracts, "articles", required="articles" in required_sections
                ),
                "questions": _load_manifest_section(manifest_contracts, "questions"),
                "runtime": runtime_payload,
                "metadata": metadata,
            }
        else:
            contracts = {
                "flags": {},
                "actions": {},
                "rules": {},
                "dedups": {},
                "due_rules": {},
                "calendar": {},
                "audit": {},
                "risk_config": {},
                "articles": {},
                "questions": {},
                "runtime": runtime_payload,
                "metadata": metadata,
            }

        return contracts

    def _validate_schemas(
        self,
        base_path: Path,
        contracts: dict,
        problems: list[str],
        *,
        strict_schemas: bool = False,
        use_framework_schemas: bool = False,
    ) -> None:
        """Validate contracts against JSON schemas if available.

        If strict_schemas=True, raise ValueError on schema load errors or validation failures.
        Validators are cached by absolute schema path for performance.
        """
        try:
            schema_dir = load_framework_layout_cached(base_path).schemas_dir
        except (FileNotFoundError, ValueError):
            schema_dir = base_path / "validation_schemas"
        if not schema_dir.exists():
            if use_framework_schemas:
                from src.app.config.paths import repo_root

                schema_dir = load_framework_layout_cached(
                    repo_root() / "frameworks" / "starter"
                ).schemas_dir
                if not schema_dir.exists():
                    return
            else:
                return

        schema_files = {
            "actions": "actions.schema.json",
            "articles": "articles.schema.json",
            "flags": "flags.schema.json",
            "rules": "rules.schema.json",
            "due_rules": "due_rules.schema.json",
            "calendar": "law_calendar.schema.json",
            "questions": "questions.schema.json",
        }

        errors_found = 0
        for contract_name, schema_file in schema_files.items():
            # Only validate questions against JSON Schema when strict_schemas is enabled
            # (e.g., CLI lint with --strict). The load() path keeps tolerating
            # malformed questions documents to preserve DX.
            if contract_name == "questions" and not strict_schemas:
                continue
            schema_path = schema_dir / schema_file
            contract_data = contracts.get(contract_name)

            if schema_path.exists() and (contract_data is not None):
                try:
                    spath = str(schema_path.resolve())
                    validator = self._schema_cache.get(spath)
                    if validator is None:
                        from jsonschema import Draft202012Validator

                        schema = json.loads(schema_path.read_text(encoding="utf-8"))
                        validator = Draft202012Validator(schema)
                        self._schema_cache[spath] = validator
                    for err in validator.iter_errors(contract_data):
                        loc = "/".join(str(x) for x in err.absolute_path) or "<root>"
                        problems.append(
                            "[SCHEMA][ERROR] "
                            f"Schema validation error for {contract_name}: {loc}: {err.message}"
                        )
                        errors_found += 1
                except ImportError:
                    # jsonschema not available in this environment — skip schema validation
                    logger.debug(
                        "jsonschema not available; skipping schema validation for %s", contract_name
                    )
                    return
                except (json.JSONDecodeError, OSError, UnicodeDecodeError, ValueError) as e:
                    problems.append(
                        f"[SCHEMA][ERROR] Schema validation error for {contract_name}: {e}"
                    )
                    errors_found += 1

        if strict_schemas and errors_found:
            raise SchemaValidationError(
                f"Schema validation failed: {errors_found} error(s) found",
                problems=[{"message": "Schema validation errors detected"}],
            )
