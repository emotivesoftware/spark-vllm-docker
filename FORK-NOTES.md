# emotivesoftware/spark-vllm-docker — fork notes

Fork of [`eugr/spark-vllm-docker`](https://github.com/eugr/spark-vllm-docker) carrying the
Emotive-specific build grafts needed to serve our on-prem models on the DGX Spark (GB10 /
sm_121) cluster. **Upstream sanctioning is driven by Allen** — the intent is to land these
(or equivalent support) in eugr upstream over time; until then this fork is the source of
truth for the images the cluster runs.

## What this fork adds (branch `emotive-grafts`, based on upstream `cf0d5f6`)

All deltas are in `Dockerfile` (see `git diff cf0d5f6..emotive-grafts -- Dockerfile`). Three grafts:

1. **Rust frontend (`vllm._rust_tool_parser`)** — installs the pinned Rust toolchain
   (`rust/rust-toolchain.toml` = 1.95) + `protobuf-compiler` + `libprotobuf-dev` +
   `perl`/`make`/`pkg-config`, sets `PROTOC_INCLUDE=/usr/include` and
   `VLLM_REQUIRE_RUST_FRONTEND=1` before the wheel build. Without a cargo toolchain vLLM
   silently omits the PyO3 tool-parser extension, so `--tool-call-parser minimax_m3` loads
   but 500s at request time. `libprotobuf-dev` + `PROTOC_INCLUDE` are required because the
   rust `vllm-server`/`vllm-rs` crates compile gRPC (`prost`/`tonic`) and import the
   well-known type `google/protobuf/struct.proto`.
2. **deep_gemm wheel** — builds + ships `deep_gemm`; DeepSeek-V4-Flash's Sparse (Lightning)
   Attention Indexer hard-requires it.
3. **b12x** — adds `b12x` to the runner's pip install (clamped-SwiGLU NvFp4 MoE kernel for
   MiniMax-M3). Paired with the **runtime** patch `_b12x_clamp_patch.py`, which is applied
   post-launch (not at build time) and lives with the ops tooling, not here — see below.

## Images built from this fork

`./build-and-copy.sh --vllm-ref <sha> -t <tag> [--copy-to <hosts>]`:
- `vllm-node` — Qwen3.6 (brain-9/10)
- `vllm-ds` — DeepSeek-V4-Flash (brain-5..8)
- `vllm-m3b` — MiniMax-M3-NVFP4 (brain-1..4; needs the Rust + b12x grafts above)

## Where the rest lives

Launch/watchdog/supervision tooling, the runtime `_b12x_clamp_patch.py`, the systemd
watchdog units, and the cluster runbook live in **`emotivesoftware/Brainiac`** under
`infra/spark-cluster/` (the operator/ops layer). This fork is the **build** layer only.

## Tracking upstream

```
git remote add upstream https://github.com/eugr/spark-vllm-docker.git
git fetch upstream
git log --oneline emotive-grafts ^upstream/main   # our deltas
git rebase upstream/main                            # when rebasing onto newer eugr
```
