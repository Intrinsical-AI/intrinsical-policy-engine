# SPDX-License-Identifier: MPL-2.0
# Copyright 2024-2026 Pablo P.C.
"""
Jinja2 templates for UI server.

This module contains all Jinja templates used by the questionnaire UI,
separated from constants.py for better maintainability and organization.
"""

# ruff: noqa: E501
# Long lines are acceptable in template strings (HTML/CSS/JS)

from __future__ import annotations

# Shared Jinja macros for rendering questions
JINJA_MACROS = r"""
{% macro render_question(q, answers) -%}
  {% set required = q.required if q.required is defined else false %}
  <div class="q" role="group" aria-labelledby="lbl_{{ q.id }}">
    <label id="lbl_{{ q.id }}" class="q-title" for="q_{{ q.id }}">
      {{ q.text }}{% if required %} <span aria-hidden="true">*</span>{% endif %}
    </label>

    {% if q.type == "yes_no" %}
      <fieldset class="row" aria-labelledby="lbl_{{ q.id }}">
        <legend class="sr-only">{{ q.text }}</legend>
        {% set base = 'q_' ~ q.id %}
        <label class="choice">
          <input
            id="{{ base }}_yes"
            type="radio"
            name="q_{{ q.id }}"
            value="yes"
            {% if answers.get(q.id) == 'yes' %}checked{% endif %}
            {% if required %}required aria-required="true"{% endif %}
          />
          <span>Sí</span>
        </label>
        <label class="choice">
          <input
            id="{{ base }}_no"
            type="radio"
            name="q_{{ q.id }}"
            value="no"
            {% if answers.get(q.id) == 'no' %}checked{% endif %}
            {% if required %}required aria-required="true"{% endif %}
          />
          <span>No</span>
        </label>
      </fieldset>

    {% elif q.type == "yes_no_unknown" %}
      <fieldset class="row" aria-labelledby="lbl_{{ q.id }}">
        <legend class="sr-only">{{ q.text }}</legend>
        {% set base = 'q_' ~ q.id %}
        <label class="choice">
          <input
            id="{{ base }}_yes"
            type="radio"
            name="q_{{ q.id }}"
            value="yes"
            {% if answers.get(q.id) == 'yes' %}checked{% endif %}
            {% if required %}required aria-required="true"{% endif %}
          />
          <span>Sí</span>
        </label>
        <label class="choice">
          <input
            id="{{ base }}_no"
            type="radio"
            name="q_{{ q.id }}"
            value="no"
            {% if answers.get(q.id) == 'no' %}checked{% endif %}
            {% if required %}required aria-required="true"{% endif %}
          />
          <span>No</span>
        </label>
        <label class="choice">
          <input
            id="{{ base }}_unk"
            type="radio"
            name="q_{{ q.id }}"
            value="unknown"
            {% if answers.get(q.id) == 'unknown' %}checked{% endif %}
            {% if required %}required aria-required="true"{% endif %}
          />
          <span>No determinado</span>
        </label>
      </fieldset>

    {% elif q.type in ["one_of", "multi"] %}
      {% set opts = q.options %}
      {% if q.type == 'one_of' %}
        <fieldset class="row" aria-labelledby="lbl_{{ q.id }}">
          <legend class="sr-only">{{ q.text }}</legend>
          {% for opt in opts %}
            {% set oid = 'q_' ~ q.id ~ '_' ~ loop.index %}
            <label class="choice">
              <input
                id="{{ oid }}"
                type="radio"
                name="q_{{ q.id }}"
                value="{{ opt.value }}"
                {% if answers.get(q.id) == opt.value %}checked{% endif %}
                {% if required %}required aria-required="true"{% endif %}
              />
              <span>{{ opt.label or opt.value }}</span>
            </label>
          {% endfor %}
        </fieldset>
      {% else %}
        {% set sel = answers.get(q.id) or [] %}
        <fieldset class="row" aria-labelledby="lbl_{{ q.id }}">
          <legend class="sr-only">{{ q.text }}</legend>
          {% for opt in opts %}
            {% set oid = 'q_' ~ q.id ~ '_' ~ loop.index %}
            <label class="choice">
              <input
                id="{{ oid }}"
                type="checkbox"
                name="q_{{ q.id }}"
                value="{{ opt.value }}"
                {% if opt.value in sel %}checked{% endif %}
                {% if required %}aria-required="true"{% endif %}
              />
              <span>{{ opt.label or opt.value }}</span>
            </label>
          {% endfor %}
        </fieldset>
      {% endif %}
    {% else %}
      <input
        aria-describedby="lbl_{{ q.id }}"
        class="input"
        type="text"
        id="q_{{ q.id }}"
        name="q_{{ q.id }}"
        value="{{ answers.get(q.id, '') }}"
        {% if required %}required aria-required="true"{% endif %}
      />
    {% endif %}
  </div>
{%- endmacro %}
"""

# Full page template for initial load
PAGE_TEMPLATE = (
    JINJA_MACROS
    + r"""
<!doctype html>
<html lang="es">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Intrinsical Policy Engine - Questionnaire</title>
    <meta name="csrf-token" content="{{ csrf_token }}" />
    <script nonce="{{ csp_nonce }}" src="https://unpkg.com/htmx.org@1.9.12"></script>
    <script nonce="{{ csp_nonce }}">
      addEventListener('htmx:configRequest', (e) => {
        const t = document.querySelector('meta[name="csrf-token"]')?.content;
        if (t) e.detail.headers['X-CSRF-Token'] = t;
      });
      // UI helpers (nonce-safe)
      function toggleCompact(){ document.documentElement.classList.toggle('compact'); }
      function copyPreview(){
        const el = document.getElementById('preview');
        if(!el) return;
        const txt = el.innerText || el.textContent || '';
        navigator.clipboard.writeText(txt).then(()=>{
          const b = document.getElementById('btn-copy');
          if(b){ b.dataset.done = "1"; setTimeout(()=>{ delete b.dataset.done; }, 1200); }
        });
      }
      // Expand/Collapse groups
      function expandAll(v=true){
        document.querySelectorAll('details.group').forEach(d => {
          if(v) d.setAttribute('open','');
          else d.removeAttribute('open');
        });
      }
    </script>
    <style>
      *{box-sizing:border-box;}
      :root{
        --bg:#0c1222;
        --bg-card:#101832;
        --bg-hover:#0e1530;
        --bg-checked:#0e1a39;
        --border:#1c2540;
        --border-strong:#2a3556;
        --fg:#e7ecf4; --fg-muted:#9aabc8; --fg-title:#d9e6ff; --fg-accent:#9ab7ff;
        --brand:#4a78ff; --brand-2:#355fd1; --ok:#2ea36a; --ok-2:#2a8d5d;
        --shadow:rgba(0,0,0,0.35);
      }
      html,body{background:var(--bg); color:var(--fg); font-family:system-ui,-apple-system,Segoe UI,Roboto,Ubuntu,Cantarell,'Helvetica Neue',Arial,'Noto Sans';}
      header{padding:20px 24px; border-bottom:1px solid var(--border); background:linear-gradient(135deg,#0f1630 0%,#1a1f3a 100%); box-shadow:0 2px 8px var(--shadow);}
      h1{font-size:20px; margin:0; font-weight:600; letter-spacing:-0.02em;}
      .container{max-width:1200px; margin:0 auto; padding:20px;}
      .layout{display:grid; grid-template-columns:minmax(0,1fr) 380px; gap:20px;}
      @media (max-width: 1100px){ .layout{grid-template-columns:1fr;} aside.side{position:relative; top:auto;} }

      .card{background:var(--bg-card); border:1px solid var(--border); border-radius:12px; padding:16px; box-shadow:0 4px 16px rgba(0,0,0,.18); transition:border-color .2s, box-shadow .2s, transform .06s;}
      .card:hover{border-color:var(--border-strong); box-shadow:0 6px 20px rgba(0,0,0,.26);}

      .toolbar{display:flex; gap:12px; align-items:center; flex-wrap:wrap;}
      .pill{display:inline-block; padding:6px 12px; border-radius:999px; border:1px solid #2a3f73; background:#142149; color:#b6cbff; font-size:12px; font-weight:500; transition:transform .12s, background .15s, border-color .15s;}
      .pill.count{background:#1e336b; color:#fff; font-weight:700; border-color:#334b85;}
      .row{display:flex; gap:10px; flex-wrap:wrap; align-items:center;}
      .muted{color:var(--fg-muted); font-size:13px;}
      .hr{height:1px; background:var(--border); margin:12px 0;}

      button,.btn{background:#172650; color:var(--fg); border:1px solid #2a417a; padding:9px 14px; border-radius:8px; cursor:pointer; font-weight:600; font-size:14px; transition:transform .06s, box-shadow .15s, border-color .15s, background .15s;}
      button:hover,.btn:hover{border-color:var(--brand-2); background:#1a2b5a; box-shadow:0 4px 12px rgba(0,0,0,.28); transform:translateY(-1px);}
      button:focus-visible{outline:3px solid rgba(74,120,255,.45); outline-offset:2px;}
      button:active{transform:translateY(0); box-shadow:0 1px 4px rgba(0,0,0,.2);}
      button.export{background:#1b3330; border-color:#325c53;}
      button.small{padding:6px 10px; font-size:12px}
      .icon{opacity:.95; width:16px; height:16px; vertical-align:middle;}

      input[type="text"], select{background:var(--bg); color:var(--fg); border:1px solid var(--border); border-radius:8px; padding:8px 12px; width:100%; font-size:14px; transition:border-color .15s, box-shadow .15s;}
      input[type="text"]:focus, select:focus{outline:none; border-color:var(--brand-2); background:var(--bg-hover); box-shadow:0 0 0 3px rgba(53,88,160,0.13);}
      input[type="radio"], input[type="checkbox"]{cursor:pointer; width:16px; height:16px; accent-color:#5580e0;}

      .q{margin:12px 0; padding:12px; border-radius:10px; border:1px solid var(--border); background:var(--bg); transition:background .15s,border-color .15s, box-shadow .15s;}
      .q:hover{border-color:var(--border-strong); background:var(--bg-hover);}
      .q:has(input:checked){border-color:var(--brand-2); background:var(--bg-checked); box-shadow:0 0 0 2px rgba(53,88,160,0.15);}

      .q-title{display:block; font-weight:500; margin-bottom:10px; color:var(--fg-title); font-size:14px;}
      .choice{display:inline-flex; align-items:center; gap:6px; cursor:pointer; padding:6px 10px; border-radius:6px; user-select:none; transition:background .12s, transform .06s;}
      .choice:hover{background:rgba(53,88,160,.08);}
      .choice input:focus-visible + span{outline:2px solid #6ea0ff; outline-offset:3px; border-radius:4px;}

      details.group{margin-bottom:14px;}
      details.group > summary{list-style:none; cursor:pointer; display:flex; align-items:center; gap:8px; color:var(--fg-accent); font-weight:600; font-size:15px;}
      details.group > summary::-webkit-details-marker{display:none;}
      details.group > summary .caret{transition:transform .15s;}
      details.group[open] > summary .caret{transform:rotate(90deg);}
      details.group .desc{margin:6px 0 8px 0;}

      aside.side{position:sticky; top:16px; height:calc(100vh - 32px); overflow:auto; display:flex; flex-direction:column; gap:16px;}

      .compact .q{padding:10px; margin:8px 0;}
      .compact .choice{padding:4px 8px;}
      .compact details.group > summary{font-size:14px}

      .stats{display:flex; gap:16px; margin-top:8px;}
      .stat-item{display:flex; flex-direction:column; gap:2px;}
      .stat-label{font-size:10px; text-transform:uppercase; letter-spacing:.06em; color:#6b7fa0; font-weight:700;}
      .stat-value{font-size:18px; font-weight:800; color:var(--fg-accent);}

      .htmx-indicator{display:none; width:16px; height:16px; border:2px solid var(--brand-2); border-top-color:transparent; border-radius:50%; animation:spin .6s linear infinite; margin-left:8px;}
      .htmx-request .htmx-indicator{display:inline-block;}
      @keyframes spin { to{ transform:rotate(360deg); } }

      #preview{white-space:pre; overflow:auto; background:var(--bg); font-family:'Courier New', monospace; font-size:13px; max-height:420px; border-radius:10px; border:1px solid var(--border); padding:12px;}
      #btn-copy[data-done="1"]::after{content:" Copiado"; font-weight:600;}

      .sr-only{position:absolute;width:1px;height:1px;padding:0;margin:-1px;overflow:hidden;clip:rect(0,0,0,0);white-space:nowrap;border:0;}
    </style>
  </head>
  <body>
    <header>
      <h1>Intrinsical Policy Engine</h1>
    </header>

    <div class="container">
      <div class="card toolbar" id="top-flags" aria-live="polite">
        <div class="pill count">Flags activos: {{ stats.flags_count }}</div>
        {% if flags %}
          <div class="row">
            {% for f in flags %}<span class="pill" title="Flag activo">{{ f }}</span>{% endfor %}
          </div>
        {% else %}
          <span class="muted">No hay flags todavía</span>
        {% endif %}
        <div style="margin-left:auto; display:flex; gap:8px; align-items:center;">
          <button type="button" class="small" onclick="toggleCompact()">Densidad</button>
          <button type="button" class="small" onclick="expandAll(true)">Expandir</button>
          <button type="button" class="small" onclick="expandAll(false)">Colapsar</button>
          <div class="htmx-indicator" id="loading-indicator" aria-hidden="true"></div>
          <form id="toolbar-actions" method="post" action="/export">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}" />
            <button type="submit" class="export">Descargar answers.json</button>
          </form>
          <button type="button"
                  hx-post="/run"
                  hx-include="#qform"
                  hx-target="#run-status"
                  hx-swap="outerHTML"
                  hx-indicator="#loading-indicator"
                  class="export">
            Generar plan
          </button>
        </div>
      </div>

      <div class="layout">
        <main class="main">
          <form id="qform"
                hx-post="/render"
                hx-target="#questionnaire"
                hx-trigger="change delay:150, input delay:150"
                hx-swap="outerHTML"
                hx-indicator="#loading-indicator">
            <input type="hidden" name="csrf_token" value="{{ csrf_token }}" />
            <div id="questionnaire">
              {% for group in groups %}
                {% set visible_questions = group.questions | selectattr('visible') | list %}
                {% if visible_questions %}
                  <details class="group card" aria-label="{{ group.title }}" {% if loop.index0 < 3 %}open{% endif %}>
                    <summary><span class="caret">▸</span> {{ group.title }}</summary>
                    {% if group.description %}
                      <div class="muted desc">{{ group.description }}</div>
                    {% endif %}
                    {% for q in visible_questions %}
                      {{ render_question(q, answers) }}
                    {% endfor %}
                  </details>
                {% endif %}
              {% endfor %}
            </div>
          </form>
        </main>

        <aside class="side">
          <div class="card preview-container">
            <div class="preview-header" style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
              <div class="preview-title" style="font-weight:600;font-size:15px;color:var(--fg-accent);">Previsualización JSON</div>
              <div class="row">
                <button id="btn-copy" type="button" class="small" onclick="copyPreview()">Copiar</button>
              </div>
            </div>
            <div id="stats" class="stats">
              <div class="stat-item">
                <span class="stat-label">Respuestas</span>
                <span class="stat-value">{{ stats.answers_count }}</span>
              </div>
              <div class="stat-item">
                <span class="stat-label">Flags</span>
                <span class="stat-value">{{ stats.flags_count }}</span>
              </div>
            </div>
            <div class="hr"></div>
            <div id="preview">{{ preview|e }}</div>
          </div>

          <div class="card" id="run-status">
            <span class="muted">Sin ejecuciones todavía.</span>
          </div>
        </aside>
      </div>
    </div>
  </body>
</html>
"""
)

# Partial template for HTMX updates
PARTIAL_TEMPLATE = (
    JINJA_MACROS
    + r"""
<div id="questionnaire">
  {% for group in groups %}
    {% set visible_questions = group.questions | selectattr('visible') | list %}
    {% if visible_questions %}
      <details class="group card" aria-label="{{ group.title }}" {% if loop.index0 < 3 %}open{% endif %}>
        <summary><span class="caret">▸</span> {{ group.title }}</summary>
        {% if group.description %}
          <div class="muted desc">{{ group.description }}</div>
        {% endif %}
        {% for q in visible_questions %}
          {{ render_question(q, answers) }}
        {% endfor %}
      </details>
    {% endif %}
  {% endfor %}
</div>

<div id="top-flags" hx-swap-oob="true" class="card toolbar" aria-live="polite">
  <div class="pill count">Flags activos: {{ stats.flags_count }}</div>
  {% if flags %}
    <div class="row">
      {% for f in flags %}<span class="pill" title="Flag activo">{{ f }}</span>{% endfor %}
    </div>
  {% else %}
    <span class="muted">No hay flags todavía</span>
  {% endif %}
  <div style="margin-left:auto; display:flex; gap:8px; align-items:center;">
    <div class="htmx-indicator" id="loading-indicator"></div>
    <button type="button" class="small" onclick="toggleCompact()">Densidad</button>
    <button type="button" class="small" onclick="expandAll(true)">Expandir</button>
    <button type="button" class="small" onclick="expandAll(false)">Colapsar</button>
    <form id="toolbar-actions" method="post" action="/export">
      <input type="hidden" name="csrf_token" value="{{ csrf_token }}" />
      <button type="submit" class="export">Descargar answers.json</button>
    </form>
    <button type="button"
            hx-post="/run"
            hx-include="#qform"
            hx-target="#run-status"
            hx-swap="outerHTML"
            hx-indicator="#loading-indicator"
            class="export">
      Generar plan
    </button>
  </div>
</div>

<div id="preview" hx-swap-oob="true">{{ preview|e }}</div>

<div id="stats" class="stats" hx-swap-oob="true">
  <div class="stat-item">
    <span class="stat-label">Respuestas</span>
    <span class="stat-value">{{ stats.answers_count }}</span>
  </div>
  <div class="stat-item">
    <span class="stat-label">Flags</span>
    <span class="stat-value">{{ stats.flags_count }}</span>
  </div>
</div>
"""
)
