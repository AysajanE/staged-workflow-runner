# Responses Runner V2 Synthetic Example Pack

This pack is the bounded proof path for the runner.

It is intentionally synthetic and small so operators can verify the engine without adopting a business-specific workflow.

## What It Exercises

- one-pass execution with sidecar structured output
- automatic two-pass carry-forward
- reviewed three-stage progression with approved review bundles
- dry-run readiness
- run-artifact writing and proof-pack regression coverage through the bundled tests

## Pack Layout

- `shared_instructions.md`
- `corpus/`
- `prompts/`
- `inputs/`
- `workflows/`
- `tools/no_tools.profile.json`
- `schemas/synthetic_summary.schema.json`

## Quick Commands

Run the proof-pack unit tests:

```bash
python -m unittest automation.tests.test_responses_runner_v2_example_pack
```

Dry-run the one-pass workflow:

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --dry-run
```

Dry-run the two-pass workflow:

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/two_pass.workflow.json \
  --dry-run
```

Dry-run the reviewed workflow:

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json \
  --dry-run
```

## What Success Looks Like

- a dry run writes `request_payload.json` and `stage_checkpoint.json`
- a live one-pass run writes `output.structured.json`
- the reviewed workflow stops at review gates until an approved bundle is supplied
- the bundled tests pass without relying on any business-specific pack

## What This Pack Is Not

This pack validates the engine contract only.

Use it to copy structure, not content. Real task packs should replace:

- prompts
- manifests
- schemas
- corpus
- review policy
- tool settings
