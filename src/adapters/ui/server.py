# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
#!/usr/bin/env python3
"""
Refactor del servidor UI (Flask + HTMX) para cuestionario.

- Seguridad básica: CSRF por cookie + cabecera, CSP estricta con nonce, cabeceras de endurecimiento.
- Accesibilidad: <fieldset>/<legend>, ids únicos por opción, atributos aria.
- Robustez: validación/saneado de respuestas (tipos y dominios de opciones), logging consistente.
- Rendimiento: caché por mtime del questions.yml, plantillas DRY con macros compartidas.
- DX: estructura create_app(), CLI clara, /healthz y endpoint JSON /api/preview.
- UX: contador de flags/respuestas, export JSON, estilos y microfeedback HTMX.

Dependencias opcionales: Flask (instalar con extras ui)
"""

from __future__ import annotations

import argparse
import base64
import contextlib
import copy
import json
import logging
import os
import secrets
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

import yaml

from src.adapters.contracts.yaml.yaml_contract_adapter import YamlContractsAdapter
from src.adapters.frameworks.layout_loader import load_framework_layout
from src.adapters.logging.fs.fs_log import FsLogger
from src.adapters.ui.templates import PAGE_TEMPLATE, PARTIAL_TEMPLATE
from src.app.config.artifact_names import (
    ANSWERS_FILE,
    LOCKFILE_NAME,
    LOGS_DIR,
    OUT_DIR,
    SUMMARY_FILE,
)
from src.app.config.constants import (
    CSRF_TOKEN_LENGTH,
    DEFAULT_ENCODING,
    JSON_MIME_TYPE,
    LOCKFILE_TIMEOUT_SECONDS,
    TRUE_VALUES,
)
from src.app.config.paths import resolve_answers_path
from src.app.use_cases.ops import render_artifacts as ops_render_artifacts
from src.app.use_cases.ops import run_export as ops_run_export
from src.app.use_cases.ops_fs import clean_out_dir as fs_clean_out_dir
from src.common.cache import CACHE_SIZE_MEDIUM, cached_medium
from src.common.hashing import sha256_directory
from src.common.io_safety import acquire_lock, atomic_write_text
from src.common.io_utils import ensure_dir
from src.common.json_utils import json_dumps_pretty
from src.domain.constants import QUESTIONS_FILE
from src.domain.services.assess_service import assess_from_bundle
from src.domain.services.questionnaire_engine import (
    ALLOWED_TYPES,
    VALID_YESNO,
    VALID_YNU,
    build_id2q,
    options_for,
    sanitize_answers_dict,
)
from src.domain.services.questionnaire_engine import _emit_flags_for_question as _emit
from src.domain.services.rule_engine import eval_ast, parse_when

if TYPE_CHECKING:
    from flask import Flask, Response

logger = logging.getLogger("ui")

try:
    import yaml  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    logger.error("[ui] Missing PyYAML dependency: uv sync --dev")
    raise

logging.basicConfig(
    level=os.environ.get("UI_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)

# --------------------------- Utilities ---------------------------


def _qs_path(contracts_dir: Path) -> Path:
    """Get the path to the questions.yml file."""
    return contracts_dir / QUESTIONS_FILE


@cached_medium
def _load_questions_cached(path: str, mtime: float) -> dict[str, Any]:
    """Load questions from file with caching based on modification time and visibility mode."""
    data = yaml.safe_load(Path(path).read_text(encoding=DEFAULT_ENCODING)) or {}
    if not isinstance(data, dict):
        logger.error("questions.yml is not a mapping in %s", path)
        sys.stderr.write("questions.yml load failed\n")
        return {"groups": []}
    return data


def _load_questions(contracts_dir: Path) -> dict[str, Any]:
    """Load questions from framework pack directory with caching."""
    qfile = _qs_path(contracts_dir)
    qdir = contracts_dir / QUESTIONS_FILE.replace(".yml", "")

    # 1) Prefer monolithic questions.yml if present (cached path)
    if qfile.exists():
        try:
            stat = qfile.stat()
        except FileNotFoundError:
            sys.stderr.write("questions.yml load failed\n")
            return {"groups": []}
        try:
            # Return deepcopy to prevent cache pollution by mutable consumers
            return copy.deepcopy(_load_questions_cached(str(qfile), stat.st_mtime))
        except yaml.YAMLError:
            sys.stderr.write("questions.yml load failed\n")
            return {"groups": []}

    # 2) Fallback to modular questions/ directory (no caching for now)
    if qdir.exists() and qdir.is_dir():
        merged: dict[str, Any] = {}
        for f in sorted(qdir.glob("*.yml")):
            try:
                data = yaml.safe_load(f.read_text(encoding=DEFAULT_ENCODING)) or {}
            except yaml.YAMLError:
                sys.stderr.write("questions.yml load failed\n")
                return {"groups": []}
            if not isinstance(data, dict):
                continue
            for key, value in data.items():
                if key not in merged:
                    merged[key] = value
                elif isinstance(merged.get(key), list) and isinstance(value, list):
                    merged[key] = list(merged[key]) + list(value)
                elif isinstance(merged.get(key), dict) and isinstance(value, dict):
                    merged[key] = {**merged[key], **value}
                else:
                    merged[key] = value
        return merged if merged else {"groups": []}

    # 3) Legacy behavior: neither questions.yml nor questions/ exist
    sys.stderr.write("questions.yml load failed\n")
    return {"groups": []}


def _parse_form_answers(form: Any, questions_doc: dict[str, Any]) -> dict[str, Any]:
    """Parse and sanitize form answers against questions_doc."""
    id2q = build_id2q(questions_doc)

    raw_answers: dict[str, Any] = {}
    for qid, q in id2q.items():
        fname = f"q_{qid}"
        qtype = q.get("type", "one_of")
        try:
            raw = form.getlist(fname) if qtype == "multi" else form.get(fname)
        except (AttributeError, TypeError):
            raw = None
        if raw is None or raw == "":
            continue
        raw_answers[qid] = raw

    return sanitize_answers_dict(questions_doc, raw_answers)


def _emit_flags_for_ui(question: dict, answer: Any, flags_acc: set[str]) -> None:
    """Emit flags for the UI preview based on sanitized answers."""
    qtype = question.get("type", "one_of")
    if qtype in ("yes_no", "yes_no_unknown"):
        if isinstance(answer, str):
            normalized = answer.strip().lower()
            if (qtype == "yes_no" and normalized in VALID_YESNO) or (
                qtype == "yes_no_unknown" and normalized in VALID_YNU
            ):
                _emit(question, normalized, flags_acc)
            else:
                _emit(question, answer, flags_acc)
        else:
            _emit(question, answer, flags_acc)
    elif qtype == "multi":
        vals = answer if isinstance(answer, list) else [answer]
        _emit(question, vals, flags_acc)
    else:
        _emit(question, answer, flags_acc)


def _is_visible(question: dict, flags_so_far: set[str]) -> bool:
    """Evaluate show_if clause for a question using the current flag set."""
    cond = question.get("show_if")
    if not cond:
        return True
    try:
        ast = parse_when(cond)
        return eval_ast(ast, flags_so_far)
    except (ValueError, KeyError, TypeError, AttributeError):
        return True


def _compute_state(
    questions_doc: dict[str, Any],
    answers: dict[str, Any],
    *,
    incremental: bool = True,
    strict_visibility: bool = True,
) -> tuple[list[dict], set[str]]:
    """Calculate visible groups and flags.

    - incremental=True (default): emit flags based on progressive visibility (traversal).
    - incremental=False: Pass A emite flags ignorando show_if sobre todas las respuestas
      saneadas; Pass B calcula visibilidad con ese set. Si strict_visibility=False, fuerza
      visible=True.
    """
    emitted: set[str] = set()

    if not incremental:
        # Pass A: emit flags over all responses (ignores show_if)
        for g in questions_doc.get("groups") or []:
            for q in g.get("questions") or []:
                if not isinstance(q, dict) or not q.get("id"):
                    continue
                qid = str(q.get("id"))
                if qid in answers:
                    try:
                        _emit_flags_for_ui(q, answers[qid], emitted)
                    except (ValueError, KeyError, TypeError) as e:
                        logger.error("[ui] emit_flags error qid=%s: %s", qid, e)

    groups_out: list[dict] = []
    for g in questions_doc.get("groups") or []:
        g_out = {
            "id": g.get("id"),
            "title": g.get("title"),
            "description": g.get("description"),
            "questions": [],
        }
        for q in g.get("questions") or []:
            if not isinstance(q, dict) or not q.get("id"):
                continue
            qid = str(q.get("id"))
            qtype = q.get("type", "one_of")
            if qtype not in ALLOWED_TYPES:
                logger.warning("Question type not allowed: %s (qid=%s)", qtype, qid)
                continue
            vis = True if (not strict_visibility) else _is_visible(q, emitted)
            qopts = options_for(q)
            q_rec = {
                "id": qid,
                "text": q.get("text"),
                "type": qtype,
                "options": qopts,
                "visible": bool(vis),
            }
            g_out["questions"].append(q_rec)
            if incremental and vis and (qid in answers):
                try:
                    _emit_flags_for_ui(q, answers[qid], emitted)
                except (ValueError, KeyError, TypeError) as e:
                    logger.error("[ui] emit_flags error qid=%s: %s", qid, e)
        groups_out.append(g_out)

    return groups_out, emitted


# --------------------------- Flask app ---------------------------


def _new_nonce() -> str:
    """Generate a random base64 nonce for CSP-compatible inline scripts."""
    return base64.b64encode(secrets.token_bytes(16)).decode()


def _set_security_headers(resp: Response, csp_nonce: str) -> Response:
    """Apply opinionated security headers (CSP, no-store, frame protections)."""
    csp = (
        "default-src 'self'; "
        "img-src 'self' data:; "
        "script-src 'self' https://unpkg.com 'nonce-" + csp_nonce + "'; "
        "style-src 'self' 'unsafe-inline'; "
        "connect-src 'self'; frame-ancestors 'none'"
    )
    resp.headers.setdefault("Content-Security-Policy", csp)
    resp.headers.setdefault("Referrer-Policy", "no-referrer")
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("X-Frame-Options", "DENY")
    resp.headers.setdefault("Cache-Control", "no-store")
    return resp


def create_app(contracts_dir: Path, answers_path: str | None = None) -> Flask:  # noqa: C901
    """Create and configure the Flask UI application."""
    from flask import (
        Flask,
        abort,
        g,
        jsonify,
        make_response,
        render_template_string,
        request,
    )

    app = Flask(__name__)
    app.config["UI_CONTRACTS_DIR"] = str(contracts_dir)
    app.config["UI_ANSWERS_PATH"] = answers_path

    # CSRF: Strict check for secret
    secret = os.environ.get("UI_CSRF_SECRET")
    if not secret:
        raise ValueError("UI_CSRF_SECRET environment variable is required for security.")
    app.config["UI_CSRF_SECRET"] = secret
    app.config["UI_API_KEY"] = os.environ.get("UI_API_KEY") or ""
    app.config["UI_VIS_INCREMENTAL"] = (
        str(os.environ.get("UI_VIS_INCREMENTAL", "1")).strip().lower() in TRUE_VALUES
    )
    app.config["UI_VIS_STRICT"] = (
        str(os.environ.get("UI_VIS_STRICT", "1")).strip().lower() in TRUE_VALUES
    )

    @app.before_request
    def _before():
        # Generate per-request nonce
        g.csp_nonce = _new_nonce()

    def _load_initial_answers() -> dict[str, Any]:
        if not answers_path:
            return {}
        rp = resolve_answers_path(answers_path)
        if not rp or not rp.exists():
            return {}
        try:
            content = rp.read_text(encoding=DEFAULT_ENCODING)
            if rp.suffix.lower() in [".yml", ".yaml"]:
                raw = yaml.safe_load(content)
            else:
                raw = json.loads(content)

            if isinstance(raw, dict) and isinstance(raw.get("answers"), dict):
                return {str(k): v for k, v in raw["answers"].items()}
        except (OSError, ValueError, json.JSONDecodeError, UnicodeDecodeError, yaml.YAMLError):
            logger.warning("answers.json/yaml inválido, ignorando")
        return {}

    initial_answers: dict[str, Any] = _load_initial_answers()

    def _get_questions_doc() -> dict[str, Any]:
        """Reload questions.yml with caching (path + mtime)."""
        cdir = Path(app.config["UI_CONTRACTS_DIR"])
        return _load_questions(cdir)

    def _compute_for(answers: dict[str, Any]) -> tuple[list[dict], set[str]]:
        """Compute groups and flags using app config for visibility settings."""
        inc = bool(app.config.get("UI_VIS_INCREMENTAL", True))
        sv = bool(app.config.get("UI_VIS_STRICT", True))
        questions_doc = _get_questions_doc()
        return _compute_state(questions_doc, answers, incremental=inc, strict_visibility=sv)

    def _render(answers: dict[str, Any]) -> Any:
        groups, flags = _compute_for(answers)
        preview = json_dumps_pretty({"answers": answers})
        csp_nonce = g.csp_nonce
        csrf_token = request.cookies.get("csrf_token")
        if not csrf_token:
            csrf_token = base64.b64encode(secrets.token_bytes(CSRF_TOKEN_LENGTH)).decode()
        stats = {
            "answers_count": len(answers),
            "flags_count": len(flags),
        }
        html: str = cast(
            str,
            render_template_string(
                PAGE_TEMPLATE,
                groups=groups,
                flags=sorted(flags),
                answers=answers,
                preview=preview,
                stats=stats,
                csp_nonce=csp_nonce,
                csrf_token=csrf_token,
            ),
        )
        resp = make_response(html)
        force_https = str(os.environ.get("UI_FORCE_HTTPS", "")).strip().lower() in {
            "1",
            "true",
            "yes",
        }
        resp.set_cookie("csrf_token", csrf_token, httponly=True, secure=force_https, samesite="Lax")
        return _set_security_headers(resp, csp_nonce)

    def _check_csrf(req) -> None:
        token_cookie = req.cookies.get("csrf_token")
        token_header = req.headers.get("X-CSRF-Token")
        try:
            token_form = req.form.get("csrf_token")
        except (RuntimeError, AttributeError, KeyError, TypeError):
            token_form = None
        token = token_header or token_form
        # Igualdad constante para evitar filtrados temporales
        if not token_cookie or not token or not secrets.compare_digest(token_cookie, token):
            abort(403)

    def _enforce_auth(req) -> None:
        """Enforce authentication: Strict API Key / Basic Auth if configured,
        else CSRF for mutations.
        """
        api_key = app.config.get("UI_API_KEY") or ""

        if api_key:
            # Strict Mode: Request MUST be authenticated via API Key or Basic Auth
            # 1. Check X-API-Key header
            hdr = req.headers.get("X-API-Key") or ""
            if hdr and secrets.compare_digest(api_key, hdr):
                return

            # 2. Check Basic Auth (user=anything, password=api_key)
            auth = req.authorization
            if auth and auth.password and secrets.compare_digest(api_key, auth.password):
                return

            # 3. Fail with 401 to trigger browser prompt if accessed via browser
            logger.warning("ui.auth.failed", extra={"client": req.remote_addr})
            resp = make_response("Authentication Required", 401)
            resp.headers["WWW-Authenticate"] = 'Basic realm="Intrinsical Policy Engine"'
            abort(resp)

        # No API Key configured: Public Mode (restricted by bind address)
        # Only enforce CSRF for state-changing methods
        if req.method not in ("GET", "HEAD", "OPTIONS", "TRACE"):
            _check_csrf(req)

    @app.after_request
    def _after(resp):
        # Inject CSP header using the per-request nonce
        # Do NOT regenerate nonce to ensure it matches what was rendered in templates
        if not resp.headers.get("Content-Security-Policy"):
            # Use g.csp_nonce if available (set in before_request), else generate one (fallback)
            nonce = getattr(g, "csp_nonce", _new_nonce())
            return _set_security_headers(resp, nonce)
        return resp

    @app.get("/healthz")
    def healthz():
        """Health check endpoint for monitoring."""
        return {"ok": True}

    @app.get("/")
    def index():
        """Main questionnaire page endpoint."""
        _enforce_auth(request)
        return _render(initial_answers)

    @app.post("/render")
    def render_partial():
        """Render questionnaire partial for HTMX updates."""
        _enforce_auth(request)
        questions_doc = _get_questions_doc()
        form_answers = _parse_form_answers(request.form, questions_doc)
        groups, flags = _compute_for(form_answers)
        preview = json_dumps_pretty({"answers": form_answers})
        csrf_token = request.cookies.get("csrf_token") or ""
        stats = {
            "answers_count": len(form_answers),
            "flags_count": len(flags),
        }
        html: str = render_template_string(
            PARTIAL_TEMPLATE,
            groups=groups,
            flags=sorted(flags),
            answers=form_answers,
            preview=preview,
            stats=stats,
            csrf_token=csrf_token,
        )
        return html

    @app.post("/api/compute")
    def api_compute():
        """Compute groups/flags for provided answers (JSON) for test-harness/non-HTMX clients."""
        _enforce_auth(request)
        payload = request.get_json(silent=True) or {}
        incoming = payload.get("answers") if isinstance(payload, dict) else None
        if not isinstance(incoming, dict):
            incoming = {}
        # sanitize answers against questions
        questions_doc = _get_questions_doc()
        answers = sanitize_answers_dict(questions_doc, incoming)
        groups, flags = _compute_for(answers)
        logger.info(
            "ui.api.compute",
            extra={
                "client": request.remote_addr,
                "answers_count": len(answers),
                "flags_count": len(flags),
            },
        )
        return jsonify({"groups": groups, "flags": sorted(list(flags)), "answers": answers})

    @app.post("/export")
    def export_answers():
        """Export current answers as answers.json (attachment)."""
        _enforce_auth(request)
        if not app.config.get("UI_API_KEY"):
            return make_response("Export disabled in Read-Only mode (UI_API_KEY required)", 403)

        questions_doc = _get_questions_doc()
        form_answers = _parse_form_answers(request.form, questions_doc)
        payload = json_dumps_pretty({"answers": form_answers})
        logger.info(
            "ui.export",
            extra={
                "client": request.remote_addr,
                "answers_count": len(form_answers),
            },
        )
        resp = make_response(payload)
        resp.headers["Content-Type"] = f"{JSON_MIME_TYPE}; charset={DEFAULT_ENCODING}"
        resp.headers["Content-Disposition"] = "attachment; filename=answers.json"
        return resp

    @app.post("/run")
    def run_pipeline():  # noqa: C901
        """Run full assess + export pipeline using current answers."""
        _enforce_auth(request)
        if not app.config.get("UI_API_KEY"):
            html = (
                "<div id='run-status' class='card' data-status='error'>"
                "<div class='muted'>Execution disabled in Read-Only mode</div>"
                "<pre style='white-space: pre-wrap; font-size: 12px;'>"
                "Set UI_API_KEY to enable /run</pre></div>"
            )
            return html

        questions_doc = _get_questions_doc()
        form_answers = _parse_form_answers(request.form, questions_doc)

        contracts_dir_local = Path(app.config["UI_CONTRACTS_DIR"]).resolve()
        templates_dir = Path(
            app.config.get("UI_TEMPLATES_DIR")
            or load_framework_layout(contracts_dir_local).templates_dir
        )

        answers_cfg = app.config.get("UI_ANSWERS_PATH")
        out_dir = (
            Path(answers_cfg).resolve().parent / OUT_DIR
            if answers_cfg
            else (Path(OUT_DIR).resolve())
        )

        logger.info(
            "ui.run.start",
            extra={
                "client": request.remote_addr,
                "answers_count": len(form_answers),
                "contracts_dir": str(contracts_dir_local),
                "out_dir": str(out_dir),
            },
        )

        if not ensure_dir(out_dir):
            html = (
                "<div id='run-status' class='card' data-status='error'>"
                "<div class='muted'>Error creating output directory</div>"
                "<pre style='white-space: pre-wrap; font-size: 12px;'>"
                "Failed to create output directory</pre></div>"
            )
            return html

        answers_path = Path(answers_cfg).resolve() if answers_cfg else (out_dir / ANSWERS_FILE)
        if not ensure_dir(answers_path.parent):
            html = (
                "<div id='run-status' class='card' data-status='error'>"
                "<div class='muted'>Error creating answers directory</div>"
                "<pre style='white-space: pre-wrap; font-size: 12px;'>"
                "Failed to create answers directory</pre></div>"
            )
            return html

        answers_payload = {"answers": form_answers}

        # Clean out_dir to start fresh, preserving answers.json if it lives under out_dir
        try:
            od = out_dir.resolve()
        except OSError:
            od = out_dir
        try:
            ap = answers_path.resolve()
        except OSError:
            ap = answers_path
        keep_list = [ap] if str(ap).startswith(str(od)) else []

        # Concurrency guard: lockfile
        lockfile = out_dir / LOCKFILE_NAME

        try:
            # Try to acquire lock with a short timeout
            with acquire_lock(lockfile, timeout=LOCKFILE_TIMEOUT_SECONDS):
                # Write answers atomically inside the lock
                try:
                    atomic_write_text(
                        answers_path, json_dumps_pretty(answers_payload), encoding=DEFAULT_ENCODING
                    )
                except OSError:
                    html = (
                        "<div id='run-status' class='card' data-status='error'>"
                        "<div class='muted'>Error saving answers.json</div>"
                        "<pre style='white-space: pre-wrap; font-size: 12px;'>"
                        "Failed to write answers.json atomically</pre></div>"
                    )
                    return html

                fs_clean_out_dir(out_dir, keep_list)

                # 2) Lint (strict)
                adapter = YamlContractsAdapter()
                problems = []
                try:
                    problems = adapter.validate(
                        str(contracts_dir_local), use_framework_schemas=True
                    )
                except (OSError, ValueError, TypeError) as e:
                    logger.exception(
                        "ui.run.lint_exception", extra={"contracts_dir": str(contracts_dir_local)}
                    )
                    html = (
                        "<div id='run-status' class='card' data-status='error'>"
                        "<div class='muted'>Lint failed</div>"
                        f"<pre style='white-space: pre-wrap; font-size: 12px;'>{e!s}</pre></div>"
                    )
                    return html

                if problems:
                    logger.warning(
                        "ui.run.lint_failed",
                        extra={
                            "contracts_dir": str(contracts_dir_local),
                            "problems_count": len(problems),
                        },
                    )
                    html = (
                        "<div id='run-status' class='card' data-status='error'>"
                        f"<div class='muted'>Lint: {len(problems)} problem(s). "
                        "Strict mode: aborted.</div>"
                        "<pre style='white-space: pre-wrap; font-size: 12px;'>"
                        f"{json.dumps(problems[:CACHE_SIZE_MEDIUM], ensure_ascii=False, indent=2)}"
                        "</pre>"
                        "</div>"
                    )
                    return html

                # 3) Assess + Export (filesystem target) with strict + save plan + logs
                try:
                    bundle = adapter.load(str(contracts_dir_local))
                    logger_fs = FsLogger(out_dir / LOGS_DIR / "app.jsonl")

                    # Compute templates_hash for traceability (INV-05, ENGINE ARCHITECTURE v1)
                    templates_hash = sha256_directory(templates_dir)
                    from src.common.hashing import compute_framework_pack_hashes
                    from src.domain.services.integrity import compute_bundle_hash

                    bundle_hash = compute_bundle_hash(bundle)
                    framework_pack_hashes = compute_framework_pack_hashes(
                        load_framework_layout(contracts_dir_local), law_data_hash=bundle_hash
                    )

                    plan = assess_from_bundle(
                        bundle,
                        answers_payload,
                        logger=logger_fs,
                        templates_hash=templates_hash,
                        framework_pack_hashes=framework_pack_hashes,
                    )
                    res = ops_run_export(
                        plan,
                        contracts_dir_local,
                        out_dir,
                        logger_fs,
                        save_plan=True,
                        templates=str(templates_dir),
                        targets=None,
                        config=None,
                        strict=True,
                    )
                except (OSError, ValueError, KeyError, TypeError) as e:
                    logger.exception(
                        "ui.run.export_failed",
                        extra={"contracts_dir": str(contracts_dir_local), "out_dir": str(out_dir)},
                    )
                    html = (
                        "<div id='run-status' class='card' data-status='error'>"
                        "<div class='muted'>Export failed</div>"
                        f"<pre style='white-space: pre-wrap; font-size: 12px;'>{e!s}</pre></div>"
                    )
                    return html

                if res.any_error:
                    extra = {
                        "contracts_dir": str(contracts_dir_local),
                        "out_dir": str(out_dir),
                        "failed_targets": list(res.target_errors),
                        "pre_artifact_error": bool(res.pre_artifact_error),
                    }
                    if getattr(res, "config_error", False):
                        extra["config_error"] = True
                        extra["config_error_msg"] = getattr(res, "config_error_msg", None)

                    logger.error("ui.run.export_has_errors", extra=extra)

                    if getattr(res, "config_error", False):
                        detail = (
                            "<div class='muted'>"
                            "Export configuration error. Check export config file and logs "
                            "for details.</div>"
                        )
                    else:
                        detail = (
                            "<div class='muted'>"
                            "Export failed (errors detected). Check logs for details."
                            "</div>"
                        )

                    html = f"<div id='run-status' class='card' data-status='error'>{detail}</div>"
                    return html

                # 4) Render secondary artifacts from summary.json (optional convenience)
                render_ok = True
                render_err = ""
                try:
                    ops_render_artifacts(
                        str(templates_dir), str(out_dir / SUMMARY_FILE), str(out_dir), strict=True
                    )
                except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
                    render_ok = False
                    render_err = str(e)
                    logger.exception(
                        "ui.run.render_failed",
                        extra={"templates_dir": str(templates_dir), "out_dir": str(out_dir)},
                    )

                # 5) Report status snippet
                parts: list[str] = []
                parts.append("<div class='muted'>Lint: OK</div>")
                parts.append(f"<div class='muted'>Export OK → <code>{out_dir}</code></div>")
                if render_ok:
                    parts.append("<div class='muted'>Render OK</div>")
                else:
                    parts.append(
                        "<div class='muted'>Render with warnings</div>"
                        "<pre style='white-space: pre-wrap; font-size: 12px;'>"
                        + render_err
                        + "</pre>"
                    )
                parts.append(f"<div class='muted'>answers.json → <code>{answers_path}</code></div>")

                render_status = "ok" if render_ok else "warn"
                html = (
                    "<div id='run-status' class='card' "
                    f"data-status='success' data-lint='ok' data-export='ok' "
                    f"data-render='{render_status}'>" + "".join(parts) + "</div>"
                )

                logger.info(
                    "ui.run.done",
                    extra={
                        "client": request.remote_addr,
                        "out_dir": str(out_dir),
                        "render_ok": render_ok,
                    },
                )
                return html

        except BlockingIOError:
            logger.warning(
                "ui.run.lock_busy",
                extra={
                    "client": request.remote_addr,
                    "out_dir": str(out_dir),
                    "lockfile": str(lockfile),
                },
            )
            return (
                "<div id='run-status' class='card' data-status='busy'>"
                "<div class='muted'>Another execution is in progress. "
                "Try again in a few seconds.</div>"
                "</div>"
            )
        except Exception as e:
            logger.exception("ui.run.unexpected_error")
            return (
                "<div id='run-status' class='card' data-status='error'>"
                "<div class='muted'>Unexpected error</div>"
                f"<pre style='white-space: pre-wrap; font-size: 12px;'>{e!s}</pre></div>"
            )

    @app.get("/api/preview")
    def api_preview():
        """Return computed state for initial answers (for tests or other clients)."""
        _enforce_auth(request)
        groups, flags = _compute_for(initial_answers)
        stats = {
            "answers_count": len(initial_answers),
            "flags_count": len(flags),
        }
        return jsonify(
            {
                "groups": groups,
                "flags": sorted(list(flags)),
                "answers": initial_answers,
                "stats": stats,
            }
        )

    @app.get("/api/health/full")
    def api_health_full():
        """Light health check: loads questions and templates and runs a minimal compute."""
        qfile = contracts_dir / QUESTIONS_FILE
        qdir = contracts_dir / QUESTIONS_FILE.replace(".yml", "")
        contracts_ok = qfile.exists() or qdir.exists()
        templates_dir_local = Path(
            app.config.get("UI_TEMPLATES_DIR") or load_framework_layout(contracts_dir).templates_dir
        )
        templates_ok = templates_dir_local.exists()
        try:
            _compute_for({})
            compute_ok = True
        except (ValueError, TypeError):
            compute_ok = False
        return jsonify(
            {
                "contracts_ok": contracts_ok,
                "templates_ok": templates_ok,
                "compute_ok": compute_ok,
            }
        )

    return app


# --------------------------- CLI ---------------------------


def run_ui_server(
    contracts_dir: Path,
    answers_path: str | None = None,
    host: str = "127.0.0.1",
    port: int = 8000,
) -> None:
    """Start the Flask questionnaire server with optional persisted answers."""
    try:
        import importlib.util as _iu

        if _iu.find_spec("flask") is None:
            raise ImportError("flask not installed")
    except ImportError:
        logger.error("[ui] Missing optional dependency: uv sync --dev")
        raise

    app = create_app(contracts_dir, answers_path)
    debug = str(os.environ.get("UI_DEBUG", "0")).strip().lower() in TRUE_VALUES
    app.run(host=host, port=int(port), debug=debug)


def cli() -> None:
    """Argument parser wrapper for running the UI server from the shell."""
    ap = argparse.ArgumentParser(description="Run the questionnaire UI server (Flask + HTMX)")
    ap.add_argument(
        "--contracts",
        required=True,
        help="Carpeta raíz del framework pack",
    )
    ap.add_argument("--answers", default=None, help="Ruta a answers.json opcional")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", default=8000, type=int)
    args = ap.parse_args()

    # Use default from constant if None, or raise error if required
    # Since original code had a default, we should probably keep it or use a constant
    run_ui_server(Path(args.contracts), args.answers, args.host, int(args.port))


if __name__ == "__main__":
    cli()


# --------------------------- Utilities ---------------------------


def clear_caches() -> None:
    """Clear internal UI caches (used mainly in tests / hot reload).

    - Limpia la caché de questions.yml basada en (path, mtime).
    - Útil en tests para forzar recarga entre casos.
    """
    with contextlib.suppress(AttributeError, RuntimeError):
        _load_questions_cached.cache_clear()  # type: ignore[attr-defined]
