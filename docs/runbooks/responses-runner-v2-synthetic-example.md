# Responses Runner V2 Synthetic Example

The synthetic pack is the bounded proof pack for the core runner. Use it to validate the engine before adapting a real task pack.

## Fast Checks

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

## Live Smoke Test

Run the one-pass workflow live and wait for completion:

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --wait
```

A successful run leaves `run_manifest.json` at status `completed` and writes, under `<run-dir>/stages/01_draft_summary/attempt_001/`:

- `artifact.md`
- `response.final.json`
- `response.latest.json`
- `uploads.json`
- `token_preflight.json`

## Reviewed Proof Path

The quickest reviewed proof path is the unit test above because it exercises stage sequencing, `human`-gate stops, and `--handoff-note` continuation with the fake client.

The bundled `reviewed_three_stage` workflow declares `human` gates on its first two stages, so a live run stops after each of them in `waiting_for_review`. Continue with `run --run-dir <run-dir> --handoff-note <note.md>`; the note plus the approved `artifact.md` reach the next stage as Reviewed Handoff Inputs via `handoff_from_stage_id`. For `reviewed` gates (one reviewer CLI, in-process, with one primary-model revision) the gstack playbook pack under `automation/task_packs/` is the reference shape, and `run --dry-run` renders every stage of either workflow under `dry_runs/`.

## When To Use This Pack

Use the synthetic pack when you want to verify:

- the CLI can dry-run a pack cleanly
- gated stages stop progression until the gate is satisfied (`human` gates here, continued with `--handoff-note`; a reviewer verdict for `reviewed` gates)
- the handoff note and approved artifact reach the next stage as Reviewed Handoff Inputs
- the run directory structure is being written correctly

Do not use the synthetic pack as production content. Copy its structure, then replace the prompts, manifests, schemas, and corpus with task-specific assets.
