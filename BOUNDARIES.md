# Boundaries

The wheel and source distribution contain the engine library and CLI.
Its supported commands are `lint`, `assess`, `export`, `seal`, and `inspect`.
Filesystem, calendar, and explicit Asana, Jira, and Linear export integrations
remain part of that core. API integrations generate offline request records;
the CLI does not deliver requests to providers.

The source checkout additionally contains the educational starter, documentation,
development scripts, and synthetic demo fixtures. Those directories are not
bundled in either artifact. Packs and evidence are external input to the installed
engine.

It excludes operational regulatory packs, private policy calibrations, commercial
evidence templates, generated real-world outputs, and private Git history.
The included evidence is synthetic acceptance data.

The starter shows how to structure and validate a pack, assess inputs, supply
evidence, export a snapshot, and seal it. It supplies no official guidance,
certification, legal advice, or guarantee of compliance.

The [EU AI Act process mock](demos/process-review/README.md) adds generic intake,
review, and signoff tasks for technical practice. It has no legal fidelity,
normative mappings, or dependency on the complete framework maintained elsewhere.
The engine contains no imports or special cases for that pack. Its local scenario
checks do not constitute an external pilot or expert acceptance.

## Provenance

This is a clean public distribution derived from prior private development.
Public releases are constrained by the source and distribution scanner and
reviewed against this boundary. Private content and reserved assets remain
outside this repository.
