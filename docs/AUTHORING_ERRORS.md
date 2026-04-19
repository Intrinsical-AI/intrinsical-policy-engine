# Authoring Errors

Common pack-authoring failures usually fall into a few categories.

## Missing Files

If lint reports a missing path, check `manifest.yml` first. Runtime files,
contract files, templates, schemas, and profiles are resolved from the manifest.

## Broken References

If an action, flag, evidence path, or node ID cannot be resolved, check that the
same stable ID appears in every file that references it.

## Rule Does Not Fire

Check the emitted flags in the trace and compare them with the rule expression.
The expression must match the final flag names exactly.

## Empty Export

Check that the selected profile applies to the current plan and that the profile
nodes point to existing templates.

## Template Failure

Run with a minimal answer file and review unresolved placeholders. Do not hide
missing values in production-style exports.
