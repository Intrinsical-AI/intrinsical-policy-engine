# Security And Limitations

Intrinsical Policy Engine is infrastructure. It is not a legal, audit, or
certification authority.

## Security Model

- Treat input answers as potentially sensitive.
- Do not include raw confidential answers in outputs unless you explicitly
  intend to retain them.
- Load only framework packs and templates controlled by a trusted authoring
  process. Pack paths and symbolic links are constrained, but Jinja templates
  are not an isolation boundary for hostile code or content.
- Review generated artifacts before sharing them.
- Use sealing as an integrity check, not as a guarantee of truth.
- Export output must not equal, contain, or sit inside its framework pack. A
  reused output tree containing symbolic links is rejected before logs,
  assessment traces, summaries, or artifacts are written.
- A strict or release workflow must treat an absent GPG signature as a failed
  signing operation. Unsigned output is supported only through an explicit
  development/CI policy and is not release evidence.

## Limitations

- The starter pack is educational only.
- No regulatory coverage is included.
- No generated output should be treated as legal advice.
- No automated decision from this tool replaces human review.
- No acceptance by any customer, authority, auditor, or internal governance body
  is implied.

## Public Release Scanner

The public repository includes `scripts/check_public_release.py`. It enforces a
strict route allowlist and a semantic denylist for private or reserved content.
