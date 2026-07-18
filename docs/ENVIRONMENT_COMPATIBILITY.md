# Environment variable compatibility in 2.x

The public CLI prefers every `IPE_*` variable shown below. For transitional
2.x compatibility only, it falls back to the paired `LEXOPS_*` variable when
the public variable is unset. If both are set, `IPE_*` wins.

This table records the current behavior; it does not make the fallback names a
permanent public API. Each fallback must be explicitly kept or removed during
the 3.0 configuration redesign.

| Public variable | Transitional fallback | Consumer | Current behavior | Security or release impact | Suggested 3.0 decision |
| --- | --- | --- | --- | --- | --- |
| `IPE_ENV` | `LEXOPS_ENV` | YAML contract adapter | Selects the default contract mode; `dev` is tolerant and other values are strict. | Can weaken schema enforcement when set to `dev`. | Remove fallback; express mode through the public execution policy. |
| `IPE_STRICT_CONTRACTS` | `LEXOPS_STRICT_CONTRACTS` | YAML contract adapter | Explicit true/false value overrides the environment-derived strict default. | Directly controls contract validation strictness. | Remove fallback; retain only the public name at the product boundary. |
| `IPE_TOLERATE_QUESTIONS_ERRORS` | `LEXOPS_TOLERATE_QUESTIONS_ERRORS` | YAML contract adapter | Allows selected question-file errors to be tolerated. | May allow an incomplete authoring input to load. | Remove fallback; replace with an explicit loader option. |
| `IPE_ALLOW_INCOMPLETE_COVERAGE` | `LEXOPS_ALLOW_INCOMPLETE_COVERAGE` | Export gate | Permits coverage gaps outside the always-tolerant development export mode. | Can bypass a release-relevant quality gate. | Remove fallback and prohibit the option in the 3.0 release policy. |
| `IPE_DEMO_MODE` | `LEXOPS_DEMO_MODE` | Export context builder | Marks generated context as demo output unless the plan supplies an explicit boolean. | Changes presentation metadata, not assessment rules. | Remove fallback; keep a typed public profile option if still required. |
| `IPE_DEV_MODE` | `LEXOPS_DEV_MODE` | CLI command registration | Shows maintainer-only CLI commands in help. | Expands the visible maintenance surface. | Remove fallback; keep the public name only if dev commands remain. |
| `IPE_OUT_DIR` | `LEXOPS_OUT_DIR` | Output path resolver | Supplies the default output directory when no CLI path is passed. | Controls a filesystem write target. | Remove fallback; validate the public path through central configuration. |
| `IPE_SKIP_GPG_SIGNING` | `LEXOPS_SKIP_GPG_SIGNING` | Filesystem manifest strategy | Skips signing for CI or development exports. | Must never silently qualify an unsigned package as a published release. | Remove fallback and model unsigned CI output explicitly in the 3.0 gate report. |

## 2.x policy

- Do not add new `LEXOPS_*` fallbacks to the public repository.
- Do not change the precedence above in a patch release.
- Record the effective security-sensitive settings in release diagnostics.
- Revisit every row during 3.0; absence of an explicit decision means removal.
