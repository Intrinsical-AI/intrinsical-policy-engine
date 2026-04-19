# Write Your First Pack

This guide uses the included starter pack.

## 1. Inspect The Layout

```text
frameworks/starter/
├── FRAMEWORK_VERSION.yml
├── manifest.yml
├── law/
├── delivery/
├── render/
├── evidence/
├── meta/
└── runtime/
```

## 2. Validate The Pack

```bash
uv run ipe lint --contracts frameworks/starter --strict
```

## 3. Run An Export

```bash
uv run ipe export \
  --contracts frameworks/starter \
  --answers demos/starter/basic/answers.json \
  --out out/starter \
  --strict-templates
```

## 4. Seal The Output

```bash
uv run ipe seal --export-dir out/starter
```

## 5. Change One Thing

Edit a question label, add a `map_to_flags` entry, or adjust the starter action
title. Then run lint and export again.

Keep changes small until the trace and output shape are easy to understand.
