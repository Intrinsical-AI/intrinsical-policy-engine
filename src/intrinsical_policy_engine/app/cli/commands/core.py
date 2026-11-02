# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""The five supported CLI commands share compiled contracts and input gates."""

from __future__ import annotations

import argparse
import json
import sys
from argparse import Namespace
from pathlib import Path

from yaml import YAMLError

from intrinsical_policy_engine.adapters.contracts.yaml.yaml_contract_adapter import (
    CompiledPack,
    YamlContractsAdapter,
)
from intrinsical_policy_engine.adapters.export.base.models.export_config import ExportSettings
from intrinsical_policy_engine.app.cli.commands._helpers import load_inputs, report_warnings
from intrinsical_policy_engine.app.cli.commands.inspection import inspected_item, rule_groups
from intrinsical_policy_engine.app.cli.diagnostics import DocumentError
from intrinsical_policy_engine.app.evidence import EvidenceSnapshot, check_ci, collect_evidence
from intrinsical_policy_engine.app.export.artifacts import write_assessment
from intrinsical_policy_engine.app.export.orchestrator import export_plan
from intrinsical_policy_engine.app.export.projection import project_plan
from intrinsical_policy_engine.app.use_cases.seal import seal_and_package
from intrinsical_policy_engine.common.document_io import read_document
from intrinsical_policy_engine.common.hashing import compute_framework_pack_hashes
from intrinsical_policy_engine.common.output_safety import fresh_output
from intrinsical_policy_engine.common.package_integrity import write_checksums
from intrinsical_policy_engine.domain.bundles.coverage import profile_coverage
from intrinsical_policy_engine.domain.contract_models import canonical_date
from intrinsical_policy_engine.domain.services.assess_service import assess_from_bundle


def _add_log_argument(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--log-jsonl",
        metavar="FILE",
        help="Append JSONL events outside pack, input and output paths (default: no log)",
    )


def register(subparsers: argparse._SubParsersAction) -> None:
    lint = subparsers.add_parser(
        "lint",
        help="Compile and validate a framework pack",
        description="Validate contracts, references and templates; print JSONL diagnostics.",
    )
    lint.add_argument("--contracts", required=True, metavar="PACK", help="Pack directory")
    lint.add_argument(
        "--strict", action="store_true", help="Treat warnings as errors (default: off)"
    )
    _add_log_argument(lint)
    lint.set_defaults(handler=_lint)
    for command in ("assess", "export"):
        parser = subparsers.add_parser(
            command,
            help=f"{command.title()} using a compiled pack",
            description=(
                "Evaluate input and write assessment metadata."
                if command == "assess"
                else "Publish a closed export with evidence and offline payloads."
            ),
            epilog="Paths are relative to the working directory. Existing outputs are preserved.",
        )
        parser.add_argument("--contracts", required=True, metavar="PACK", help="Pack directory")
        parser.add_argument(
            "--answers", required=True, metavar="INPUT", help="JSON/YAML answers or flags envelope"
        )
        parser.add_argument(
            "--out", required=True, metavar="DIR", help="New output directory (must not exist)"
        )
        parser.add_argument(
            "--strict", action="store_true", help="Treat warnings as errors (default: off)"
        )
        parser.add_argument(
            "--check-ci",
            action="store_true",
            help="Require real ready evidence and profile coverage (default: off)",
        )
        parser.add_argument(
            "--evidence-dir", metavar="DIR", help="Explicit evidence source (default: none)"
        )
        parser.add_argument(
            "--base-date",
            metavar="YYYY-MM-DD",
            help="Required for relative calendar dates; no implicit current date",
        )
        parser.add_argument(
            "--include-raw-input",
            action="store_true",
            help="Include answers and user metadata (default: redacted)",
        )
        _add_log_argument(parser)
        if command == "export":
            parser.add_argument(
                "--target",
                nargs="+",
                default=["filesystem"],
                metavar="TARGET",
                help="Offline targets: filesystem, asana, jira, linear (default: filesystem)",
            )
            parser.add_argument(
                "--config", metavar="FILE", help="Strict JSON/YAML exporter configuration"
            )
            parser.add_argument(
                "--profile",
                action="append",
                metavar="ID",
                help="Repeat for several IDs; exclusive with config profiles (default: all)",
            )
        parser.set_defaults(handler=_assess if command == "assess" else _export)
    seal = subparsers.add_parser(
        "seal",
        help="Verify baseline, sign and optionally package",
        description="Verify a closed export against its baseline; signing requires local GPG.",
    )
    seal.add_argument("--export-dir", required=True, metavar="DIR", help="Closed export directory")
    seal.add_argument(
        "--output", metavar="FILE", help="New ZIP outside the export (default: no ZIP)"
    )
    seal.add_argument(
        "--no-sign",
        action="store_true",
        help="Prevent new signing; still verify existing signature (default: sign)",
    )
    seal.add_argument(
        "--strict", action="store_true", help="Treat warnings as errors (default: off)"
    )
    _add_log_argument(seal)
    seal.set_defaults(handler=_seal)
    inspect = subparsers.add_parser(
        "inspect",
        help="Inspect a compiled pack",
        description="Print JSON: authoring inputs, rules, calendar, or a compiled rule/action.",
        epilog="Example: ipe inspect --contracts frameworks/starter action STARTER-CONTROL-REVIEW",
    )
    inspect.add_argument("--contracts", required=True, metavar="PACK", help="Pack directory")
    inspect.add_argument(
        "inspect_args",
        nargs="*",
        metavar="SELECTOR",
        help=(
            "Optional: action ID, rule ID, or rule FAMILY ID. "
            "Families: derivation, stop, role, tier, outcome, routing, due, metric"
        ),
    )
    _add_log_argument(inspect)
    inspect.set_defaults(handler=_inspect)


def _lint(args: Namespace) -> int:
    problems = YamlContractsAdapter().validate_detailed(args.contracts)
    for problem in problems:
        print(json.dumps(problem.to_dict(), ensure_ascii=False))
    errors = sum(problem.severity == "error" for problem in problems)
    warnings = len(problems) - errors
    failed = bool(errors or (args.strict and warnings))
    sys.stderr.write(
        f"Pack {'invalid' if failed else 'valid'}: {args.contracts} "
        f"({errors} errors, {warnings} warnings)\n"
    )
    if failed:
        sys.stderr.write("Hint: Fix the reported source and field, then run lint --strict again.\n")
    if problems and args.logger is not None:
        args.logger.warning(
            "pack.diagnostics",
            {
                "errors": errors,
                "warnings": warnings,
                "problem_codes": sorted({p.code for p in problems}),
            },
        )
    return int(failed)


def _assessment(
    args: Namespace, settings: ExportSettings | None = None
) -> tuple[CompiledPack, dict, EvidenceSnapshot]:
    if args.base_date is not None:
        canonical_date(args.base_date)
    pack, raw, source_hash = load_inputs(args)
    if settings is not None and settings.profiles is not None:
        unknown = set(settings.profiles) - set(pack.bundle_profiles)
        if unknown:
            raise ValueError(f"Unknown profiles: {sorted(unknown)}")
    hashes = compute_framework_pack_hashes(pack.layout)
    plan = assess_from_bundle(
        pack.bundle,
        raw,
        logger=args.logger,
        include_full_trace=True,
        base_date=args.base_date,
        framework_pack_hashes=hashes,
        templates_hash=hashes["render_templates_hash"],
    )
    plan["trace"]["answers_raw"]["source_file_sha256"] = source_hash
    evidence = collect_evidence(plan, Path(args.evidence_dir) if args.evidence_dir else None)
    return pack, plan, evidence


def _assess(args: Namespace) -> int:
    pack, plan, evidence = _assessment(args)
    if args.check_ci:
        problems = check_ci(plan, evidence, profile_coverage(pack.bundle_profiles, plan))
        if problems:
            raise ValueError("\n".join(problems))
    with fresh_output(Path(args.out)) as staging:
        write_assessment(project_plan(plan, include_raw_input=args.include_raw_input), staging)
        write_checksums(staging)
    print(f"Assessment written: {args.out}")
    print(
        f"Selected actions: {len(plan['actions'])}; outcome: {', '.join(plan['outcome']) or 'none'}"
    )
    print("Review _metadata/summary.json; export to collect evidence and seal a closed snapshot.")
    return 0


def _export(args: Namespace) -> int:
    try:
        raw_config = read_document(Path(args.config)) if args.config else {}
        settings = ExportSettings.model_validate(raw_config)
    except (OSError, ValueError, UnicodeError, YAMLError) as exc:
        raise DocumentError("Configuration", args.config or "default settings", exc) from exc
    if args.profile is not None:
        if settings.profiles is not None:
            raise ValueError("Select profiles in either --profile or config, not both")
        settings.profiles = args.profile
    # All target configuration is checked before evaluating the assessment.
    for target in args.target:
        settings.for_target(target)
    if len(args.target) != len(set(args.target)):
        raise ValueError("Duplicate export targets")
    pack, plan, evidence = _assessment(args, settings)
    result = export_plan(
        plan=plan,
        pack=pack,
        destination=Path(args.out),
        targets=args.target,
        settings=settings,
        evidence=evidence,
        include_raw_input=args.include_raw_input,
        check_ci_enabled=args.check_ci,
        strict=args.strict,
        logger=args.logger,
    )
    print(f"Export written: {result.outdir} ({result.files} files)")
    print(f"Selected actions: {len(plan['actions'])}; targets: {', '.join(result.targets)}")
    print("Review _metadata/summary.json and CHECKSUMS.sha256 before sealing.")
    return 0


def _seal(args: Namespace) -> int:
    result = seal_and_package(
        export_dir=Path(args.export_dir),
        output_zip=Path(args.output) if args.output else None,
        sign=not args.no_sign,
        strict=args.strict,
    )
    if not result.success:
        raise ValueError("\n".join(result.errors))
    for warning in result.warnings:
        sys.stderr.write(f"Warning: {warning}\n")
    if result.warnings and args.logger is not None:
        args.logger.warning("seal.warnings", {"count": len(result.warnings)})
    print(f"Seal verified: {result.files_validated} files")
    print(
        f"Signature verified: {result.signature_fingerprint}"
        if result.signature_fingerprint
        else "Signature: unsigned"
    )
    if args.output:
        print(f"ZIP written: {args.output}")
    return 0


def _inspect(args: Namespace) -> int:
    pack = YamlContractsAdapter().compile(args.contracts)
    report_warnings(pack.problems, args.logger)
    if not args.inspect_args:
        print(
            json.dumps(
                {
                    "framework_id": pack.bundle.manifest.framework_id,
                    "version": pack.bundle.manifest.version,
                    "requires_engine": pack.bundle.manifest.requires_engine,
                    "actions": [action.id for action in pack.bundle.actions.actions],
                    "profiles": sorted(pack.bundle_profiles),
                    "rules": {
                        family: [rule.id for rule in rules]
                        for family, rules in rule_groups(pack.bundle).items()
                    },
                    "calendar": pack.bundle.calendar.model_dump(mode="json"),
                    "flags": [flag.model_dump(mode="json") for flag in pack.bundle.flags.registry],
                    "questions": pack.bundle.questions.model_dump(mode="json")["groups"],
                    "evidence": pack.bundle.model_dump(mode="json")["evidence_map"],
                    "action_evidence": {
                        action.id: list(action.evidence) for action in pack.bundle.actions.actions
                    },
                },
                indent=2,
            )
        )
        return 0
    item = inspected_item(pack.bundle, args.inspect_args)
    print(json.dumps(item.model_dump(mode="json"), indent=2))
    return 0
