# DSpark reasoning / grammar boundary backport (BRAIN-2255)

Draft image correction for vLLM's thinking + structured-output + speculative
decoding boundary. No running service or model assignment is changed by this
directory. Never run `--apply` inside a serving container.

## Incident and correction

Brain-12's direct multi-turn thinking-on / required-tool probe generated prose
and native DSML instead of the required JSON tool list; the required parser then
returned no calls. Thinking-off / required and thinking-on / auto controls
returned valid calls. The dropped calls are a downstream symptom: recovering
DSML at the parser does not restore the engine's output constraints.

The reported build is `0.21.1rc1.dev339+g1967a5627bc3`. Its upstream source
predates these merged fixes:

- [vLLM #44297](https://github.com/vllm-project/vllm/pull/44297):
  constrain speculative positions after the reasoning marker, handle padded
  bonus rows, and trim reasoning out of scheduler grammar advancement.
- [vLLM #44993](https://github.com/vllm-project/vllm/pull/44993):
  use the scheduler's exact accepted-token delta instead of placeholder math,
  record the boundary for all constraint types, and advance the verified suffix.

This strongly matches the incident, but the reported version does not establish
the custom image's installed source or its exact speculative/async launch flags.
Inspect those before attributing the live failure definitively to this engine
bug. The previous parser-recovery candidate is superseded; this candidate keeps
required JSON parsing and every tool-selection contract intact.

## Backport scope

Only three vLLM engine files change: the structured-output manager, its
request-state dataclass, and the scheduler's accept-side call site. The two
draft-validation callers retain the upstream placeholder fallback.

The three boundary helpers are copied exactly from #44993's final head
`891d7afdc6e439dc60299680780326afd1071175`. The bitmask loop includes #44297's
logic. Two compatibility adaptations preserve the May base: keep its existing
speculative-token allocation API and its unconditional bonus row, because the
newer diffusion API is absent there. No unrelated newer vLLM code is transplanted.

`patch.json` pins complete before/after SHA-256 hashes for all three files at
`1967a5627bc3710b680bbec24ecb99aaddedf22b`. Unknown source, ambiguous anchors
and repeated application fail closed. All files are checked and compiled before
any write. The adjacent `*.diff` files show the exact resulting source changes.

## CPU regression

Python 3.10+; standard library only:

```bash
python3 -m unittest discover -s fixes/dspark-reasoning-grammar -v
```

Tests execute actual pinned manager methods and the scheduler's grammar-advance
block. Strict FSM, tokenizer-marker and tensor substitutes exercise accepted-token
bookkeeping, mid-window masks, rollback, padded rows, error termination and
non-thinking controls. Baseline tests reproduce the missed marker and missing
masks. These are CPU boundary tests, not real xgrammar/guidance, GPU decoding,
production request-shape qualification or Plan-quality evidence.

Fixtures retain the vLLM Apache-2.0 license. `manager.py`, `scheduler.py` and
`request.py` are unmodified source from the May base; `upstream_manager.py`
is unmodified source from the #44993 head above.

## Inspect, build and qualify

Brain-12's changed SSH host key must be verified out of band before host access.
Do not bypass verification. The installed image, source files and launch flags
have not yet been inspected; no candidate image has been built or activated.

Inspect both ranks' immutable image IDs/digests, speculative method/token count,
async scheduling configuration and all runtime source mounts. Preserve existing
DSpark/vision kernels, weights, flags and mounts. Check that no mount overrides
the three patched files. This command is read-only on an inspected image:

```bash
python3 -B apply_fix.py
```

An unknown-source rejection requires re-deriving and testing the patch against
the installed files. Never weaken the hash guard to force application.

Build on ARM64 with the inspected original image:

```bash
docker build --platform linux/arm64 --build-arg BASE_IMAGE=INSPECTED_IMAGE_ID \
  -t dspark:brain-2255-reasoning-grammar fixes/dspark-reasoning-grammar
```

Before separately authorized activation, run the actual vLLM grammar/backend
regressions and isolated generation against the derived image. Confirm complete
strict JSON tool output with thinking on and speculation/async enabled, and
repeat with speculation off as a control. Preserve old image IDs and launch
specifications for rollback.

After authorized activation, repeat the multi-turn required-tool reproduction
through the real TLS/proxy route, qualify full plan.discovery artifact admission,
and smoke-test the shared chat orchestrator with thinking off / automatic tools.
Do not change chat's thinking default or relax required-tool policy. Do not close
BRAIN-2255 on CPU tests alone.

Merging this draft does not authorize activation or a service restart.
