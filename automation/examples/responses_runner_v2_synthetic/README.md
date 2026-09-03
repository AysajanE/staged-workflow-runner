# Responses Runner V2 Synthetic Example Pack

This pack is the bounded proof path for the runner.

It is intentionally synthetic and small so operators can verify the engine without adopting a business-specific workflow.

## What It Exercises

- one-pass execution with a single terminal stage
- automatic two-pass carry-forward
- three-stage progression through `human` gates, carrying the approved artifact and handoff note forward via `handoff_from_stage_id`
- dry-run readiness
- run-artifact writing and proof-pack regression coverage through the bundled tests
- workflow manifest v2 with `critical` assurance and explicit 700000-token input budgets
- durable GPT-5.6 model roles and current 30-minute prompt-cache options

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

- a dry run renders every stage under `dry_runs/stages/` (`input_manifest.json`, `input_manifest.md`, `request_payload.json`, `upload_inputs/`), with placeholder handoffs under `dry_runs/stubs/`
- a live one-pass run writes `artifact.md` and `response.final.json` under `stages/01_draft_summary/attempt_001/` and leaves `run_manifest.json` at status `completed`
- the reviewed workflow stops at each `human` gate in `waiting_for_review` until `run --run-dir <run-dir> --handoff-note <note.md>` continues it (`reviewed` gates are satisfied in-process by a reviewer verdict)
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
