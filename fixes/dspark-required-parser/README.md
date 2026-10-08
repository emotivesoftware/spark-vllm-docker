# DSpark required-tool parser correction (BRAIN-2255)

Prepared image correction. No running service, assignment, model weights,
thinking setting or request contract is changed by these files. Do not run
`--apply` inside a serving container.

## Defect and correction

Direct TLS probes on Brain-12 (reported vLLM
`0.21.1rc1.dev339+g1967a5627bc3`) reproduced a complete native DeepSeek DSML
tool call being discarded with thinking on and `tool_choice="required"`.
The response had `finish_reason="tool_calls"` but an empty tool-call array.
Thinking off emitted a JSON tool list that parsed correctly; thinking on
with automatic selection parsed DSML correctly.

The required branch validates only JSON and clears failed output. The
correction retains that output after a JSON validation failure and lets the
existing configured native parser extract it. It reuses the existing parser
dispatch without changing the request, generation constraints, parser flags,
reasoning, streaming, or the served model. Successful required JSON, named
tool calls and chat's automatic-selection branch preserve their existing path.
Multiple native calls remain multiple calls; Brainiac's admission rules still
own terminal cardinality and artifact validation.

The full source-file SHA-256 must match the pinned upstream commit exactly:
`a7bca64cebc8670f9644e7ca8a729e5841b07a8e9e2cd6a50aaf886e5a9536a3`.
Unknown source, missing/duplicate anchors and repeated application are refused.
The actual Brain-12 installed file and image ID have **not** been inspected:
SSH host-key verification needs resolution first. Matching the API's reported
version does not establish that its source is unmodified. This is a candidate
fix, not a verified deployment artifact.

## Local regression

Requires Python 3.10+ and Pydantic 2 (no vLLM installation or GPU):

```bash
python3 -m unittest discover -s fixes/dspark-required-parser -v
```

Fixtures are unmodified Apache-2.0 vLLM source at `1967a5627bc3`, used only by
tests. Tests execute its serving method, DSML decoder and schema utilities;
protocol wrappers and tokenizer plumbing are test substitutes. They reproduce
the discarded-call baseline and verify recovery, unchanged JSON/named/auto/none
paths, preserved duplicates, absent/truncated calls and fail-closed patching.
This is response-parsing evidence, not GPU generation or Plan-quality evidence.

Before building, compare in memory with the incident image's actual tokenizer
and dependencies (no weight loading or completion requests):

```bash
CUDA_VISIBLE_DEVICES= PYTHONDONTWRITEBYTECODE=1 python3 -B apply_fix.py \
  --validate /data/models/DeepSeek-V4-Flash-Vision-Exp
```

This validates the installed source hash, reproduces the before/after DSML
result with real protocol classes and checks the required JSON and automatic
DSML controls. It writes nothing. Resolve a source-hash mismatch by inspecting
the exact installed source and reauthoring/retesting the patch; never weaken the
guard to force application.

## Build and activation

Build a derived image on an ARM64 build host using the **inspected** existing
Brain-12 image's immutable digest or local image ID:

```bash
docker build --platform linux/arm64 --build-arg BASE_IMAGE=INSPECTED_IMAGE_ID \
  -t dspark:brain-2255-required-parser fixes/dspark-required-parser
```

The derived image has not been built or activated. Inspect rank-0 and rank-1
launch specifications, all runtime source mounts (including the vision patch),
and the base image before activation. Confirm no mount hides the patched
serving file. Preserve both prior image IDs and launch specifications for
rollback. Keep every other flag, kernel, mount and model unchanged.

After separately authorized activation, repeat the multi-turn thinking-on /
required probe, the thinking-off / required control and chat-style thinking-off
/ auto control through the real TLS/proxy route. Verify full discovery native
submission and semantic admission separately. Include an end-to-end chat smoke
test because Brain-12 is shared with the orchestrator. Do not claim live safety
or close BRAIN-2255 on CPU parsing tests alone.

Merging this change does not authorize activation or a service restart.
