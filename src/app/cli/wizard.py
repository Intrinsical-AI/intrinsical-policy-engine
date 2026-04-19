# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Interactive Compliance Wizard.

Acts as an interview interface to capture user input for document placeholders.
Uses Jinja2 AST parsing to robustly detect 'fill(...)' calls.
"""

from pathlib import Path
from typing import Any

from src.adapters.frameworks.layout_loader import load_framework_layout_from_path
from src.app.rendering.templating import ArtifactAssembler, extract_fill_placeholders
from src.common.io_utils import read_text_safe


class ComplianceWizard:
    """Interactive CLI wizard that gathers fill() inputs from templates."""

    def __init__(self, templates_dir: Path, plan: dict[str, Any]):
        """Initialize the wizard with the templates directory and plan context."""
        self.templates_dir = templates_dir
        self.plan = plan
        self.user_inputs: dict[str, str] = {}
        # Use assembler for environment loading
        self.assembler = ArtifactAssembler(templates_dir, strict=False)

    def _get_active_templates(self) -> list[Path]:
        """Return templates referenced by the plan's active actions."""
        active_files = set()

        # 1. Extraer evidencias requeridas por las acciones activas del plan
        actions_evidence = self.plan.get("actions_evidence_map", {})
        active_actions = self.plan.get("actions", [])

        for action_id in active_actions:
            evidences = actions_evidence.get(action_id, [])
            for ev_path in evidences:
                # Normalizar ruta
                layout = load_framework_layout_from_path(self.templates_dir)
                evidence_root = layout.evidence_templates_dir
                full_path = evidence_root / ev_path
                if full_path.exists() and full_path.is_file():
                    active_files.add(full_path)

        return sorted(list(active_files))

    def _smart_prompt(self, placeholder: str, context_hint: str) -> str:
        """Prompt the user while applying heuristics for sensible defaults."""
        default_val = ""

        # Heurísticas de "Smart Defaults"
        placeholder_lower = placeholder.lower()

        if "system name" in placeholder_lower or "system.name" in context_hint:
            default_val = self.plan.get("system", {}).get("name", "")
        elif "yyyy-mm-dd" in placeholder_lower or "date" in placeholder_lower:
            from datetime import datetime

            default_val = datetime.now().strftime("%Y-%m-%d")
        elif "owner" in placeholder_lower:
            default_val = self.user_inputs.get("default_owner", "")

        prompt_text = f"  > {placeholder}"
        if context_hint:
            prompt_text += f" (Contexto: {context_hint})"

        if default_val:
            user_in = input(f"{prompt_text} [{default_val}]: ").strip()
            return user_in if user_in else default_val
        else:
            return input(f"{prompt_text}: ").strip()

    def run_interview(self) -> dict[str, str]:
        """Collect fill() values for active templates and return a knowledge base."""
        print("\n-- INICIANDO WIZARD DE CUMPLIMIENTO --")
        fingerprint = self.plan.get("plan", {}).get("fingerprint", "N/A")
        print(f"Plan Fingerprint: {fingerprint[:8]}...")

        active_templates = self._get_active_templates()
        print(f"📂 Documentos activos detectados: {len(active_templates)}\n")

        knowledge_base: dict[str, str] = {}

        for tmpl_path in active_templates:
            content = read_text_safe(tmpl_path)
            if not content:
                continue

            # Use Robust AST extraction instead of Regex
            placeholders = extract_fill_placeholders(self.assembler.env, content)

            if not placeholders:
                continue

            print(f"\nProcesando: {tmpl_path.name}")

            for ph in placeholders:
                key = ph.strip()

                if key in knowledge_base:
                    continue

                answer = self._smart_prompt(key, tmpl_path.name)
                if answer:
                    knowledge_base[key] = answer

                    if "owner" in key.lower() and "default_owner" not in self.user_inputs:
                        self.user_inputs["default_owner"] = answer

        print("\nEntrevista completada.")
        return knowledge_base
