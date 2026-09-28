# Production model recipes: follow-up for Allen / Robbie

**Date:** 2026-09-28

**Status:** Proposal for later review; not an approved platform change

**Intended reviewers:** Allen Craig and Robbie Hudson

## Immediate priority: get the current model working

[PR #2](https://github.com/emotivesoftware/spark-vllm-docker/pull/2) is merged.
Use its narrowly scoped, pinned parser correction as the near-term path for
brain-14/15. This proposal is **not a prerequisite** for building or activating
that correction, and does not call for a model swap or a new optimization
exercise during incident recovery.

The patch was tested against the installed vLLM dependencies and local tokenizer
in a separate CPU process: 32 before/after cases and four native tool-decoding
checks passed, alongside five local regression tests. That is not a completed
deployment or GPU-generation verification. At the time of this note, activation
has not been verified in this investigation. Follow the
[patch runbook](../fixes/qwen38-parser/README.md): preserve both ranks' exact
launch specifications and logs, retain rollback, and validate the real request
path after an explicitly approved rollout.

## The larger issue

A production model is more than its weights. Its behavior depends on the
weights and tokenizer, serving image and patches, chat template, tool/reasoning
parsers, launch flags, hardware topology, and request configuration together.
We need a reproducible, tested record of that combination.

Brain-14 illustrated the gap: it generated tokens and exposed the expected
model, yet its shared-parser shortcut bypassed required named-tool constraints.
The live launch selected `qwen3_xml` and `qwen3`; the correction restores the
adapters that attach those constraints. Availability and general generation
checks alone did not prove the contract Brainiac needed.

Allen's review raised the right integration question: a parser patch passing
in isolation does not establish that a complete optimized recipe will launch
and behave correctly. A subsequent review of #2 confirmed the pair's image and
source hashes match the pins and that it uses a hand-written serve script,
without runtime vLLM modifications. Its actual launch remains the baseline.

There is earlier fleet evidence too. The **September 4** inventory in
[IT-314](https://emotivesoft.atlassian.net/browse/IT-314) recorded different image
builds under identical tags, including differing builds across a TP pair. That
dated finding is not a claim about today's entire fleet. The one-time archive
is complete; recurring archive-on-discovery is separately tracked in
[IT-315](https://emotivesoft.atlassian.net/browse/IT-315). Recoverable images are
valuable, but do not by themselves establish reproducible builds or launches.

## Proposed direction

Every **production self-hosted deployment** should have a versioned serving
release, using the existing recipe tooling where suitable. Standardize the
process and evidence, not every model's flags or kernels. Hosted providers are
outside the image/launch portion. Keep experiments explicitly separate from
production promotion.

Each release should identify:

- Weight, tokenizer and chat-template revisions; quantization and model patches.
- Immutable image digest, build-source revisions and applied serving patches.
- Recipe revision, resolved launch arguments and non-secret environment values.
- Secret references only, never secret values in Git or evidence bundles.
- Hardware requirements, tensor/pipeline parallel layout, rank membership,
  ports, mounts, resource limits and restart behavior.
- Tested request settings and capabilities, workload results, and rollback target.

A deployment record should reference that release and its tests. Observed state
should be compared with the declared release, not silently accepted as a new
desired configuration. Existing emergency changes must be recorded and reconciled.

## Suggested implementation sequence

1. **Inventory before migration.** Reuse SparkOps' captured launch data and
   supplement hidden wrapper-script arguments through a scoped inspection.
   Record actual image identities and all ranks. Flag missing evidence rather
   than guessing. No fleet-wide relaunch is needed to do this.
2. **Represent today's configuration first.** Use brain-14/15 as a pilot once
   the immediate repair is working. Capture its current FP8 configuration and
   approved patch as a reproducible recipe/release without changing performance
   settings at the same time.
3. **Validate the complete serving contract.** Test named/required/auto/none
   tool choices as applicable, argument schemas, thinking settings, streaming
   where consumed, representative long contexts, and realistic concurrency.
   Use both CPU checks and real generation through the deployed proxy path.
   Then run the actual consumer workloads. Report first-attempt validity,
   retry-assisted success and semantic correctness separately.
4. **Evaluate optimization as a separate candidate.** Compare upstream recipes
   against the reproducible baseline. The currently published upstream
   [Qwen3.8 Flash Next cluster recipe](https://github.com/eugr/spark-vllm-docker/blob/main/recipes/qwen3.8-flash-next-nvfp4-cluster.yaml)
   uses NVFP4 weights and B12X kernels, unlike this FP8 deployment. It is not a
   drop-in launch cleanup. Pin the evaluated upstream commit and measure quality,
   first-attempt success, latency, sustained throughput, memory and stability.
5. **Promote and observe one deployment at a time.** Preserve an approved
   rollback artifact and launch specification; coordinate maintenance and
   in-flight work; verify every rank; run post-activation contract checks.
   Extend drift reporting to include the serving release identity. Avoid a
   fleet-wide conversion before the pilot demonstrates recovery and repeatability.

“Optimized” should mean more reliable useful work within the required latency
and resource budget, not merely higher raw token throughput. A recipe-file
validation or dry-run is not application qualification.

## Ownership to confirm, not a new platform to build

The existing split in
[IT-313](https://emotivesoft.atlassian.net/browse/IT-313) provides the starting
point: SparkOps owns fleet discovery, routing, health and restart operations;
Brainiac/Relay owns application-side model registration and role assignment.

- **Allen / serving-build maintainers:** review supported recipes, image builds,
  parser/kernel combinations and model-specific performance evidence.
- **Robbie / infrastructure:** review artifact recovery/distribution, controlled
  activation, captured launch fidelity, maintenance and drift visibility through
  the existing SparkOps tooling.
- **Brainiac maintainers:** own request-contract compatibility, qualification
  evidence and role assignments. Other consumers need their own workload evidence.

This proposal does not change existing qualification/Apply policy, mutate a
published profile, create another fleet controller, or authorize a deployment.
Any new promotion gate or operational authority needs an explicit decision.

## Questions for later review

1. Which maintained branch/revision of the existing recipe/build tooling should
   be the production baseline, and who approves upstream updates?
2. What is the smallest release record that joins existing recipe, image,
   SparkOps and consumer evidence without duplicating their registries?
3. Is the current archive/distribution mechanism sufficient for the pilot, or
   does a registry solve a demonstrated distribution/recovery gap?
4. Which correctness, performance and rollback checks are required for promotion,
   and what isolated capacity or maintenance window is available to run them?

**Pilot completion criterion:** an operator can reproduce the approved serving
configuration, identify it across every rank, verify the consumer contract and
restore the previous release without reconstructing commands from chat history.
