# Synthetic Repository Fact Sheet

This fact sheet is low-authority synthetic context used by the bounded proof pack.

## Runner Facts

- repository package name: `staged-workflow-runner`
- internal Python package path: `automation/responses_runner_v2`
- primary-generation default model role: durable `gpt-5.6` alias with `reasoning.mode=pro`
- structural-processing default model role: durable `gpt-5.6` alias in standard mode
- GPT-5.6 prompt caching: implicit mode with `ttl=30m`
- one workspace root per invocation
- synthetic workflows are examples, not production task packs

## Gated Handoff Facts

Reviewed synthetic workflows exercise:

- `human` gates that stop the run in `waiting_for_review`;
- continuation with `run --run-dir <run-dir> --handoff-note <note.md>`;
- carry-forward of the approved `artifact.md` via `handoff_from_stage_id`;
- the human handoff note (or reviewer notes at a `reviewed` gate) travelling with it as Reviewed Handoff Inputs;
- lower-authority prior-stage context via `reference_context_from_stage_ids`.

## Migration Notes

The separate supervisor lane was removed; a stage is now judged by one reviewer CLI verdict or a human handoff note. This file stays in the model migration scan because stale model facts here would otherwise defeat the repository's static migration check and example-pack documentation consistency.
