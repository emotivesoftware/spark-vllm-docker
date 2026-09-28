# Qwen shared-parser constraint fix (BRAIN-2163)

This is a **prepared image correction, not a deployment**. It makes no model,
assignment, prompt, token-budget, or retry-policy change. No running service is
patched by the build. Do not run `--apply` inside a serving container.

## Confirmed defect

On 2026-09-28 brain-14's `qwen38-tp2` container ran
`0.1.dev20073+g8e685d198`, with `--tool-call-parser qwen3_xml` and
`--reasoning-parser qwen3`. Its `ParserManager.get_parser()` returned the raw
shared `Qwen3Parser` engine, bypassing `DelegatingParser.adjust_request()` and
its structural-tag setup. Named/required tool requests therefore had **no
decoding constraint**, even with `strict: true`. Explicit JSON-schema requests
did attach constraints. The same CPU-only render probe on brain-4 (v0.25.1)
attached named/required Qwen XML constraints correctly.

The fix backports the functional change from
[vLLM #52830](https://github.com/vllm-project/vllm/pull/52830): preserve the
reasoning and tool adapters instead of returning their shared raw engine.
The unused private helper remains to minimize the vendor-source change.

The image digest and source-file SHA-256 are pinned to the inspected artifact.
Unknown source, missing/duplicate anchors, and repeated application fail closed.
There is no fuzzy patch, downloaded patch at build time, or broad vLLM upgrade.

## Verification

Local stdlib regression tests:

```bash
python3 -m unittest discover -s fixes/qwen38-parser -v
```

Read-only before/after comparison on the **unpatched** incident image, with its
local tokenizer available (does not load weights or call a completion API):

```bash
CUDA_VISIBLE_DEVICES= PYTHONDONTWRITEBYTECODE=1 python3 -B apply_fix.py \
  --validate /data/models/Qwen3.8-Flash-Next-FP8
```

Both variants execute in a separate Python process's memory. This checks named,
required, auto and none choices; thinking on/off; strict omitted (the current
Brainiac request) and strict true. It checks that thinking settings survive and
that constrained Qwen XML still decodes into native tool calls and arguments.
The installed source is checked unchanged afterward.

Initial installed-dependency validation reproduced the missing constraints and
restored them after the correction. **This is not GPU generation evidence** and
does not prove semantic plan quality. Brainiac currently omits `strict`; do not
claim that restoring named-tool enforcement makes every argument satisfy the
full schema. That is a separate request-contract decision.

## Build and controlled activation

Build on an ARM64 build host, not inside a serving container:

```bash
docker build --platform linux/arm64 \
  -t qwen38-flash-next:parser-52830 \
  fixes/qwen38-parser
```

The Dockerfile uses the inspected base manifest digest. Its source-file hash
guard verifies that the resolved platform image contains the expected code.
The resulting parser file SHA-256 is
`ff23f89e148e3de8c23a78d7c9242ea3531bcaa86287d0c985fc1318a78bbfbc`.

The derived Docker image has not yet been built or activated. Activation is a
separate operator-approved operation: drain in-flight work, preserve both
ranks' logs and exact launch specifications, distribute the same verified image
to brain-14 and brain-15, and recreate the pair with all existing launch flags
and mounts unchanged. Preserve the prior image and launch specification for
rollback. Do not use an unrelated cluster launcher or swap model weights.

After activation, verify the **full production render tool** through the real
TLS/proxy path: required named-call constraints must be present, and bounded
first-attempt completions must return the named tool without corrective retry.
Then run the actual plan qualification and report first-attempt structural
validity separately from semantic correctness and any repaired results.

No deployment or restart is authorized by this document or by merging this fix.
