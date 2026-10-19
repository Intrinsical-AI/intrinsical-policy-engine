# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""Strict file hashes and deterministic hashes of captured pack bytes."""

from __future__ import annotations

import hashlib
import json

from intrinsical_policy_engine.domain.framework_layout import FrameworkLayout


def hash_files(files: dict[str, bytes]) -> str:
    manifest = {name: hashlib.sha256(data).hexdigest() for name, data in sorted(files.items())}
    return hashlib.sha256(
        json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def compute_framework_pack_hashes(
    layout: FrameworkLayout, *, law_data_hash: str | None = None
) -> dict[str, str]:
    files = layout.source_bytes

    def component(prefix: str) -> str:
        return hash_files(
            {name: data for name, data in files.items() if name.startswith(prefix + "/")}
        )

    contract_paths = {
        path.relative_to(layout.framework_dir).as_posix()
        for paths in layout.contract_files.values()
        for path in paths
    }
    profile_paths = {
        path.relative_to(layout.framework_dir).as_posix() for path in layout.bundle_profile_files
    }
    result = {
        "framework_id": layout.manifest.framework_id,
        "framework_version": layout.manifest.version,
        "law_data_hash": law_data_hash
        if law_data_hash is not None
        else hash_files({name: files[name] for name in contract_paths}),
        "render_templates_hash": component(layout.manifest.templates_dir),
        "evidence_templates_hash": component(layout.manifest.evidence_templates_dir),
        "bundle_profiles_hash": hash_files({name: files[name] for name in profile_paths}),
        "manifest_file_hash": hashlib.sha256(files["manifest.yml"]).hexdigest(),
        "framework_pack_hash": hash_files(files),
    }
    return result
