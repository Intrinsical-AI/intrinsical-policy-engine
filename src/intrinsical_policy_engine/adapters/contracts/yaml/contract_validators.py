# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Business rule validators for contract bundles.

VALIDATION LAYER 2: Semantic Validation (Business Rules)
=========================================================
This module contains semantic validation logic that goes beyond structural validation.
It validates relationships, graph properties, and domain-specific constraints that
require access to the complete bundle context.
"""

from __future__ import annotations

import re
from typing import Any, cast

from intrinsical_policy_engine.adapters.frameworks.layout_loader import load_framework_layout
from intrinsical_policy_engine.domain.services.rule_engine import analyze_when, validate_when
from intrinsical_policy_engine.domain.validation.evidence_validator import (
    validate_evidence_map_integrity,
)

# E07: Pre-compiled regex for flag ID validation (moved from method to module level)
_VALID_FLAG_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.]+$")
_RESERVED_WORDS = frozenset({"and", "or", "not", "has", "any", "all"})


class ContractBusinessValidator:
    """Validates business rules and semantic constraints in contract bundles."""

    def validate_all(
        self, contracts: dict, base_path: Any = None, evidence_map: dict | None = None
    ) -> list[str]:
        """Run all business validations on contract bundle.

        Args:
            contracts: Dictionary of contract data
            base_path: Path object or string (optional, needed for evidence validation)
            evidence_map: Dictionary of evidence map (optional)
        """
        problems: list[str] = []

        # Defensively normalize types for validators
        flags = self._as_dict("flags", contracts.get("flags"), problems)
        actions = self._as_dict("actions", contracts.get("actions"), problems)
        articles = self._as_dict("articles", contracts.get("articles"), problems)
        dedups = self._as_dict("dedups", contracts.get("dedups"), problems)
        rules = self._as_dict("rules", contracts.get("rules"), problems)
        questions = self._as_dict("questions", contracts.get("questions"), problems)
        due_rules = self._as_dict("due_rules", contracts.get("due_rules"), problems)
        calendar = self._as_dict("calendar", contracts.get("calendar"), problems)

        # Run all business validations
        self.validate_cycles(flags, rules, problems)
        self.validate_registry_sanity(flags, problems)
        self.validate_cross_references(actions, articles, problems)
        self.validate_flag_usage(flags, actions, rules, problems)
        self.validate_question_flags(questions, flags, problems)
        self.validate_due_rules(due_rules, calendar, problems)
        self.validate_dedups(dedups, actions, problems)
        self.validate_legal_refs(actions, problems)
        self.validate_uniqueness(flags, actions, questions, problems)
        self.validate_identifiers(contracts, problems)

        # Evidence map validation always runs if data exists (INV-B2).
        # The caller (YamlContractsAdapter) decides if errors are fatal via strict mode.
        if evidence_map and base_path:
            self.validate_evidence_integrity(contracts, str(base_path), evidence_map, problems)
            self.validate_evidence_path_canon(actions, evidence_map, problems)

        return problems

    def validate_evidence_path_canon(
        self, actions: dict, evidence_map: dict, problems: list[str]
    ) -> None:
        """Fail on deprecated evidence path prefixes (e.g., any/ -> common/)."""
        deprecated: list[str] = []

        # evidence_map entries
        for entries in evidence_map.values():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                path = entry.get("path") if isinstance(entry, dict) else entry
                if isinstance(path, str) and path.startswith("any/"):
                    deprecated.append(path)

        # actions evidence entries
        for action in actions.get("actions", []) or []:
            if not isinstance(action, dict):
                continue
            for path in action.get("evidence", []) or []:
                if isinstance(path, str) and path.startswith("any/"):
                    deprecated.append(path)

        if deprecated:
            unique = sorted(set(deprecated))
            for path in unique:
                problems.append(
                    f"[PATH][ERROR] Deprecated evidence path prefix 'any/': {path} (use 'common/')"
                )

    def validate_evidence_integrity(
        self, contracts: dict, base_path: str, evidence_map: dict, problems: list[str]
    ) -> None:
        """Adapter to call domain validator with raw dicts."""
        from dataclasses import dataclass

        # Mock objects to match domain interfaces expected by the validator
        @dataclass
        class MockArticle:
            id: str

        @dataclass
        class MockArticles:
            taxonomy: list  # Note: validator expects .taxonomy, not .articles

        @dataclass
        class MockAction:
            id: str

        @dataclass
        class MockActions:
            actions: list  # Note: validator expects list of objects with .id

        @dataclass
        class MockBundle:
            evidence_map: dict
            articles: MockArticles
            actions: MockActions
            path: str

        # Extract data
        articles_data = contracts.get("articles") or {}
        actions_data = contracts.get("actions") or {}

        # Adapt Articles: taxonomy is a list in the YAML
        raw_taxonomy = articles_data.get("taxonomy") or []
        mock_article_list = [
            MockArticle(id=cast(str, a.get("id")))
            for a in raw_taxonomy
            if isinstance(a, dict) and a.get("id")
        ]
        mock_articles = MockArticles(taxonomy=mock_article_list)

        # Adapt Actions: convert list of dicts to list of MockAction
        raw_actions_list = actions_data.get("actions") or []
        mock_action_list = [
            MockAction(id=cast(str, a.get("id")))
            for a in raw_actions_list
            if isinstance(a, dict) and a.get("id")
        ]
        mock_actions = MockActions(actions=mock_action_list)

        mock_bundle = MockBundle(
            evidence_map=evidence_map, articles=mock_articles, actions=mock_actions, path=base_path
        )

        # Call validator
        evidence_dir = load_framework_layout(base_path).evidence_templates_dir
        new_problems = validate_evidence_map_integrity(mock_bundle, evidence_dir)  # type: ignore[arg-type]
        problems.extend(new_problems)

    def _as_dict(self, name: str, value: Any, problems: list[str]) -> dict:
        if isinstance(value, dict):
            return value
        if value is None:
            return {}
        problems.append(
            f"{name} must be an object; got {type(value).__name__}. Normalized to empty."
        )
        return {}

    def validate_cycles(self, flags: dict, rules: dict, problems: list[str]) -> None:
        """Detect cycles in flag derivations.

        [SMART DETECTION UPDATE]
        1. Only considers POSITIVE dependencies (ignores flags inside 'not').
        2. Respects SELF-GUARDS: If a rule sets flag 'A' but has a condition 'not: has: A',
           it assumes this rule cannot contribute to a cycle involving 'A', because
           it effectively disables itself if 'A' is present.
        """
        try:
            derivations = rules.get("derivations", []) or []
            graph: dict[str, set[str]] = {}

            # 1. Collect all known flags
            reg_ids = {
                f.get("id")
                for f in (flags or {}).get("registry", [])
                if isinstance(f, dict) and f.get("id")
            }
            derived_ids: set[str] = set()
            for d in derivations:
                if isinstance(d, dict):
                    derived_ids.update(d.get("set_flags") or [])

            all_known_ids = reg_ids | derived_ids

            def _expand_prefix(pref: str) -> set[str]:
                return {
                    flag_id
                    for flag_id in all_known_ids
                    if isinstance(flag_id, str)
                    and (flag_id == pref or flag_id.startswith(pref + "."))
                }

            # Build derivation graph
            for d in derivations:
                if not isinstance(d, dict):
                    continue

                when_expr = d.get("when")

                # 1. Extract positive sources
                src_flags, src_prefixes = self._get_positive_dependencies(when_expr)
                srcs = set(src_flags)
                for pref in src_prefixes:
                    srcs |= _expand_prefix(pref)

                # 2. Extract guarded flags (flags that appear in 'not' conditions)
                guarded_flags, guarded_prefixes = self._get_negative_constraints(when_expr)
                guards = set(guarded_flags)
                for pref in guarded_prefixes:
                    guards |= _expand_prefix(pref)

                # 3. Process targets
                targets = d.get("set_flags") or []

                for target in targets:
                    # SMART CHECK: If the rule sets 'A', but guards against 'A' (if not A...),
                    # then this specific edge cannot form a runtime cycle for A.
                    if target in guards:
                        continue

                    # Add edges from each source to this target
                    for s in srcs:
                        # Avoid self-loops (rare but possible)
                        if s == target:
                            continue
                        graph.setdefault(s, set()).update([target])

            # DFS cycle detection
            visited: set[str] = set()
            stack: set[str] = set()
            path: list[str] = []

            def _dfs(v: str) -> bool:
                visited.add(v)
                stack.add(v)
                for w in graph.get(v, set()):
                    if w in stack:
                        path[:] = [w, v]
                        return True
                    if w not in visited and _dfs(w):
                        path.append(v)
                        return True
                stack.remove(v)
                return False

            for node in list(graph.keys()):
                if node not in visited and _dfs(node):
                    problems.append(f"Cycle detected in derivations: {' -> '.join(reversed(path))}")
                    break

        except Exception as e:  # noqa: BLE001
            problems.append(f"Derivation check error: {e}")

    def _get_positive_dependencies(self, condition: Any) -> tuple[set[str], set[str]]:
        """Recursively extract flags/prefixes ONLY from positive logic branches.
        Ignores 'not' blocks.
        Returns: (flags, prefixes)
        """
        flags: set[str] = set()
        prefixes: set[str] = set()

        # Handle string conditions using analyze_when (e.g., "has('a')")
        if isinstance(condition, str):
            try:
                str_flags, str_prefixes = analyze_when(condition)
                return set(str_flags), set(str_prefixes)
            except Exception:  # noqa: BLE001
                return flags, prefixes

        if not isinstance(condition, dict):
            return flags, prefixes

        if "has" in condition:
            val = condition["has"]
            if isinstance(val, str):
                flags.add(val)
            elif isinstance(val, list):
                flags.update(val)

        if "any_prefix" in condition:
            val = condition["any_prefix"]
            if isinstance(val, str):
                prefixes.add(val)

        for op in ["any", "all"]:
            if op in condition and isinstance(condition[op], list):
                for child in condition[op]:
                    c_flags, c_prefixes = self._get_positive_dependencies(child)
                    flags.update(c_flags)
                    prefixes.update(c_prefixes)

        return flags, prefixes

    def _get_negative_constraints(self, condition: Any) -> tuple[set[str], set[str]]:
        """Recursively extract flags/prefixes that act as GUARDS (inside 'not').
        Returns: (flags, prefixes) found inside 'not' blocks.
        """
        flags: set[str] = set()
        prefixes: set[str] = set()

        if not isinstance(condition, dict):
            return flags, prefixes

        # If we hit a 'not', we extract the POSITIVE dependencies inside it
        # because those are the effective guards.
        if "not" in condition:
            # We use _get_positive_dependencies on the content of 'not'
            # to find what is being guarded against.
            n_flags, n_prefixes = self._get_positive_dependencies(condition["not"])
            flags.update(n_flags)
            prefixes.update(n_prefixes)

        # Recurse into 'all' (guards can be nested in ANDs)
        # Note: We typically don't recurse into 'any' for guards because
        # 'any' logic is ambiguous for blocking (one might be true, others false).
        # But 'all' logic preserves the constraint.
        if "all" in condition and isinstance(condition["all"], list):
            for child in condition["all"]:
                c_flags, c_prefixes = self._get_negative_constraints(child)
                flags.update(c_flags)
                prefixes.update(c_prefixes)

        return flags, prefixes

    def validate_cross_references(self, actions: dict, articles: dict, problems: list[str]) -> None:
        action_list = actions.get("actions", []) or []
        if not isinstance(action_list, list):
            return

        article_ids = {
            a.get("id")
            for a in (articles.get("taxonomy", []) or [])
            if isinstance(a, dict) and a.get("id")
        }
        action_ids = {a.get("id") for a in action_list if isinstance(a, dict) and a.get("id")}
        art_by_id = {
            t.get("id"): t
            for t in (articles.get("taxonomy", []) or [])
            if isinstance(t, dict) and t.get("id")
        }

        for action in action_list:
            if not isinstance(action, dict):
                continue
            action_id = action.get("id")
            if not action_id:
                continue

            for art_ref in action.get("articles", []) or []:
                if art_ref not in article_ids:
                    problems.append(f"Action {action_id} references unknown article: {art_ref}")
                elif art_ref in art_by_id:
                    # Optional symmetry check (disabled for now)
                    pass

            for related_id in action.get("related_actions", []) or []:
                if related_id not in action_ids:
                    problems.append(
                        f"Action {action_id} references unknown action in related_actions: "
                        f"{related_id}"
                    )

    def validate_flag_usage(
        self, flags: dict, actions: dict, rules: dict, problems: list[str]
    ) -> None:
        registry = flags.get("registry", []) or []
        known_flags = {f.get("id") for f in registry if isinstance(f, dict) and f.get("id")}
        known_prefixes = {
            fid.split(".")[0] for fid in known_flags if isinstance(fid, str) and "." in fid
        }

        def check_flag(flag_id: str, context: str) -> None:
            if not flag_id or not isinstance(flag_id, str):
                return
            if flag_id not in known_flags:
                prefix = flag_id.split(".")[0] if "." in flag_id else flag_id
                if prefix not in known_prefixes:
                    problems.append(f"{context} uses unknown flag: {flag_id}")

        for action in actions.get("actions", []) or []:
            if not isinstance(action, dict):
                continue
            action_id = action.get("id", "<unknown>")
            when = action.get("when")
            if when:
                try:
                    validate_when(when)
                    has_flags, prefixes = analyze_when(when)
                    for flag in has_flags:
                        check_flag(flag, f"Action {action_id}")
                    for prefix in prefixes:
                        base = prefix.rstrip(".*")
                        if base not in known_prefixes and base not in known_flags:
                            display_prefix = f"{base}.*" if not prefix.endswith(".*") else prefix
                            problems.append(
                                f"Action {action_id} uses unknown prefix {display_prefix}"
                            )
                except Exception as exc:  # noqa: BLE001
                    # Report invalid when expressions
                    problems.append(f"Action {action_id} has invalid when expression: {exc}")

        action_ids = {
            a.get("id")
            for a in (actions.get("actions", []) or [])
            if isinstance(a, dict) and a.get("id")
        }

        for pack in rules.get("packs", []) or []:
            if not isinstance(pack, dict):
                continue
            pack_id = pack.get("id", "<unknown>")
            when = pack.get("when")
            if when:
                try:
                    validate_when(when)
                    has_flags, prefixes = analyze_when(when)
                    for flag in has_flags:
                        check_flag(flag, f"Pack {pack_id}")
                    for prefix in prefixes:
                        base = prefix.rstrip(".*")
                        if base not in known_prefixes and base not in known_flags:
                            display_prefix = f"{base}.*" if not prefix.endswith(".*") else prefix
                            problems.append(f"Pack {pack_id} uses unknown prefix {display_prefix}")
                except Exception as exc:  # noqa: BLE001
                    # R2 Fix: Report invalid when expressions
                    problems.append(f"Pack {pack_id} has invalid when expression: {exc}")

            for action_id in pack.get("add_actions", []) or []:
                if action_id not in action_ids:
                    problems.append(f"Pack {pack_id} references unknown action {action_id}")

        for deriv in rules.get("derivations", []) or []:
            if not isinstance(deriv, dict):
                continue
            deriv_id = deriv.get("id", "<unknown>")
            when = deriv.get("when")
            if when:
                try:
                    validate_when(when)
                    has_flags, prefixes = analyze_when(when)
                    for flag in has_flags:
                        check_flag(flag, f"Derivation {deriv_id}")
                    for prefix in prefixes:
                        base = prefix.rstrip(".*")
                        if base not in known_prefixes and base not in known_flags:
                            display_prefix = f"{base}.*" if not prefix.endswith(".*") else prefix
                            problems.append(
                                f"Derivation {deriv_id} uses unknown prefix {display_prefix}"
                            )
                except Exception as exc:  # noqa: BLE001
                    # R2 Fix: Report invalid when expressions
                    problems.append(f"Derivation {deriv_id} has invalid when expression: {exc}")

            target_flags = deriv.get("set_flags", []) or []
            for target_flag in target_flags:
                if (
                    target_flag not in known_flags
                    and target_flag.split(".")[0] not in known_prefixes
                ):
                    problems.append(f"Derivation {deriv.get('id')} sets unknown flag {target_flag}")

    def validate_uniqueness(
        self, flags: dict, actions: dict, questions: dict, problems: list[str]
    ) -> None:
        seen_f: set[str] = set()
        for f in flags.get("registry", []) or []:
            if isinstance(f, dict):
                fid = f.get("id")
                if fid:
                    if fid in seen_f:
                        problems.append(f"Duplicate flag id: {fid}")
                    else:
                        seen_f.add(fid)

        seen_a: set[str] = set()
        for a in actions.get("actions", []) or []:
            if isinstance(a, dict):
                aid = a.get("id")
                if aid:
                    if aid in seen_a:
                        problems.append(f"Duplicate action id: {aid}")
                    else:
                        seen_a.add(aid)

        seen_q: set[str] = set()
        for group in questions.get("groups", []) or []:
            if isinstance(group, dict):
                for q in group.get("questions", []) or []:
                    if isinstance(q, dict):
                        qid = q.get("id")
                        if qid:
                            if qid in seen_q:
                                problems.append(f"Duplicate question id: {qid}")
                            else:
                                seen_q.add(qid)

    def validate_legal_refs(self, actions: dict, problems: list[str]) -> None:
        raw_actions = actions.get("actions") if isinstance(actions, dict) else []
        if not isinstance(raw_actions, list):
            problems.append("actions.actions must be an array; normalized to empty")
            raw_actions = []

        for action in raw_actions:
            if not isinstance(action, dict):
                continue
            aid = action.get("id", "<noid>")
            if not str(aid).startswith("Internal:") and not action.get("legal_refs"):
                problems.append(f"Action {aid} missing legal_refs")

    def validate_dedups(self, dedups: dict, actions: dict, problems: list[str]) -> None:
        action_ids = {
            a.get("id")
            for a in (actions.get("actions", []) or [])
            if isinstance(a, dict) and a.get("id")
        }
        aliases: set[str] = set()
        for m in dedups.get("mappings", []) or []:
            if isinstance(m, dict) and m.get("alias"):
                aliases.add(m.get("alias", ""))

        for mapping in dedups.get("mappings", []) or []:
            alias = mapping.get("alias")
            canonical = mapping.get("canonical")

            if not alias or not canonical:
                problems.append("Dedup mapping must include both 'alias' and 'canonical'")
                continue
            if alias == canonical:
                problems.append(f"Dedup alias==canonical for {alias}")
            if canonical in aliases:
                problems.append(f"Dedup alias points to alias: {alias} -> {canonical}")
            if canonical not in action_ids:
                problems.append(f"Dedup canonical missing in actions: {canonical}")

    def validate_registry_sanity(self, flags: dict, problems: list[str]) -> None:
        registry = flags.get("registry", []) or []
        for entry in registry:
            flag_id = entry.get("id")
            if not flag_id or not isinstance(flag_id, str):
                problems.append("Registry entry with missing or non-string id")
                continue
            if flag_id.endswith("."):
                problems.append(f"Invalid registry id with trailing dot: {flag_id}")
            if ".." in flag_id or any(seg == "" for seg in flag_id.split(".")):
                problems.append(f"Invalid registry id with empty segment: {flag_id}")

    def validate_question_flags(self, questions: dict, flags: dict, problems: list[str]) -> None:
        if not questions:
            return
        registry_ids = {
            f.get("id") for f in (flags.get("registry", []) or []) if isinstance(f, dict)
        }
        prefixes = {
            flag_id.split(".")[0]
            for flag_id in registry_ids
            if isinstance(flag_id, str) and "." in flag_id
        }

        def _check_emitted(flag_id: str, question_id: str) -> None:
            if not flag_id:
                return
            if (flag_id not in registry_ids) and (flag_id.split(".")[0] not in prefixes):
                problems.append(f"Question {question_id} emits unknown flag {flag_id}")

        for group in questions.get("groups", []) or []:
            if not isinstance(group, dict):
                continue
            for q in group.get("questions", []) or []:
                if not isinstance(q, dict):
                    continue
                question_id = q.get("id", "<unknown>")
                mapping = q.get("set_flags_on") or {}
                for lst in mapping.values():
                    for flag_id in lst or []:
                        _check_emitted(flag_id, question_id)
                mapping2 = q.get("map_to_flags") or {}
                for lst in mapping2.values():
                    for flag_id in lst or []:
                        _check_emitted(flag_id, question_id)

    def validate_due_rules(self, due_rules: dict, calendar: dict, problems: list[str]) -> None:
        if not due_rules:
            return
        from intrinsical_policy_engine.domain.services.duedate_service import flatten_calendar

        cal_map = flatten_calendar(calendar)
        cal_keys = set(cal_map.keys()) if isinstance(cal_map, dict) else set()

        if (due_rules.get("rules") or []) and not cal_map:
            problems.append("Calendar is empty or invalid while due_rules exist")

            # If the calendar is empty/invalid but rules reference specific keys,
            # surface those keys explicitly to help pinpoint configuration issues.
            missing_keys: set[str] = set()
            for rule in due_rules.get("rules", []) or []:
                if not isinstance(rule, dict):
                    continue
                for key in rule.get("calendar_keys", []) or []:
                    if key:
                        missing_keys.add(str(key))

            if missing_keys:
                keys_str = ", ".join(sorted(missing_keys))
                problems.append(f"Calendar is empty or invalid; missing calendar keys: {keys_str}")

        for rule in due_rules.get("rules", []) or []:
            if not isinstance(rule, dict):
                continue
            rule_id = rule.get("id", "<unknown>")
            for key in rule.get("calendar_keys", []) or []:
                if key not in cal_keys:
                    problems.append(f"Due rule {rule_id} references unknown calendar key {key}")

    def validate_identifiers(self, contracts: dict, problems: list[str]) -> None:
        """Validate flag IDs and dedup mappings (H-01, H-02)."""
        # H-01: Validate flag IDs
        flags_data = contracts.get("flags") or {}
        flags_registry = flags_data.get("registry", []) if isinstance(flags_data, dict) else []

        for entry in flags_registry:
            flag_id = entry.get("id")
            if not flag_id:
                continue

            if not _VALID_FLAG_ID_PATTERN.match(flag_id):
                problems.append(
                    f"[FLAGS][ERROR] Invalid flag ID '{flag_id}': "
                    f"must match pattern {_VALID_FLAG_ID_PATTERN.pattern}"
                )

            if flag_id.lower() in _RESERVED_WORDS:
                problems.append(
                    f"[FLAGS][ERROR] Invalid flag ID '{flag_id}': cannot use reserved word"
                )

        # H-02: Validate dedup canonical IDs
        dedups = contracts.get("dedups", {})
        mappings = dedups.get("mappings", []) if isinstance(dedups, dict) else []

        actions_data = contracts.get("actions") or {}
        actions = actions_data.get("actions", []) if isinstance(actions_data, dict) else []
        action_ids = {a["id"] for a in actions if isinstance(a, dict) and "id" in a}

        for mapping in mappings:
            canonical = mapping.get("canonical")
            alias = mapping.get("alias")
            if canonical and canonical not in action_ids:
                # Warn but don't fail hard, as it might be a cross-bundle reference
                problems.append(
                    f"[DEDUP][WARN] Canonical ID '{canonical}' for alias '{alias}' "
                    "not found in actions catalog"
                )
