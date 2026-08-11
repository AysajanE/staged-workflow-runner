# Final Packet Review Prompt

## Objective

Review the terminal implementation packet for direct materialization readiness.

## Required Packet Contents

Verify that the packet includes:

- root `AGENTS.md`;
- supervisor CLI entrypoint;
- supervisor orchestration modules;
- supervisor session schema;
- internal supervisor task pack;
- operator Codex prompt;
- Codex review-agent prompt;
- Claude review-agent prompt;
- consolidation prompt/policy;
- command templates for `codex exec` and `claude -p`;
- tests for model migration, session creation, scaffold staging, review gating, agent invocation, consolidation, selective acceptance, failure recovery, incomplete blocking, and final bundle creation;
- documentation/runbook updates.

## Inventory And File-Block Parity

Check that:

- file inventory paths match emitted file blocks exactly;
- actions are correct;
- no emitted file is omitted from inventory;
- no inventory row lacks a file block;
- patches are apply-ready;
- full replacement files are complete.

## Model Migration

Verify:

- primary generation defaults use durable `gpt-5.6` with `reasoning.mode=pro`;
- structural defaults use `gpt-5.6` in standard mode;
- committed GPT-5.6 workflows explicitly use implicit cache mode with `ttl=30m`;
- model caps and tests cover 128000 max output where locked;
- no unallowlisted legacy model references remain in runtime/config surfaces.

Treat verbosity and terminal-stage high/xhigh changes as unresolved experiments unless A/B evidence is present.

## Tests And Docs

Verify:

- red checks are real pre-change failures;
- green checks pass post-change;
- docs commands match implemented CLI;
- failure policies and human pauses are operational;
- review-agent JSON transport is explicit.

## Output Contract

Emit JSON conforming to `responses_runner_v2.reviewer_output.v1`, with explicit final approval or non-approval. Include blocking issues, improvements, evidence, recommendations, unsupported claims, and next action. The supervisor adds report paths and the decision envelope.

## Stopping Conditions

Do not approve if:

- placeholders or partial files remain;
- file inventory and file blocks differ;
- model migration is incomplete;
- review-agent prompts are non-machine-ingestible;
- consolidation can make final acceptance;
- operator selective acceptance is missing;
- output-limit incomplete can auto-progress;
- failed-no-artifact rerun can occur without archive;
- final bundle cannot be applied without reinterpretation.
