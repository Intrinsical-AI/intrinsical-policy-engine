# Minimum Authoring Path

A small pack needs a clear path from inputs to reviewed work:

1. Declare stable flag names.
2. Define typed questions that emit those flags.
3. Add positive derivations and put each selection condition on `action.when`.
4. Map selected actions to evidence requirements.
5. Define delivery profiles and templates using the `answers` context.
6. Lint the pack and test one complete assessment and export.
7. Supply reviewed evidence explicitly, check it, and seal the snapshot.

The starter demonstrates this path with a single action family and a synthetic
evidence fixture. Keep the first pack similarly small: one rule area, one
questionnaire, one answer example, and one complete acceptance flow.

Questions execute in declaration order before derivations. A later question
must use direct emissions from earlier questions to activate. Define expected
conclusions independently before changing the rules or refreshing fixtures.

Version the pack in its format-3 `manifest.yml`, declare `requires_engine`, and
keep policy in pack files. Establish human review and acceptance criteria before
making coverage claims.

The [EU AI Act process mock](../demos/process-review/README.md) adds an ordered
questionnaire, stops, routing, and fictional deadlines to that same path. It is
educational and independent of the complete framework maintained elsewhere.
Use its [scenario corpus](../demos/process-review/expected/scenarios.json) for
technical practice; it contains no expert or pilot acceptance.
