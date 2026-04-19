# Policy Schema

Intrinsical Policy Engine packs keep policy in declarative files owned by the
pack.

For public starter use, the stable policy surface is intentionally small:

- flags define facts the engine can reason about;
- questions emit flags from structured answers;
- rules select actions from flags;
- actions describe work to perform;
- evidence maps connect actions or topics to documents;
- delivery profiles decide which files are exported.

Do not encode private business policy directly in engine code. Keep pack policy
versioned, reviewable, and testable.

The starter pack should be treated as an authoring example, not as a normative
schema for a regulated domain.
