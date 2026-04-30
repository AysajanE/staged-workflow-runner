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

A successful run should produce:

- `response.final.md`
- `response.final.json`
- `output.structured.json`
- `sidecar.response.json`
- `sidecar.response.md`

## Reviewed Proof Path

The quickest reviewed proof path is the unit test above because it exercises stage sequencing, review bundles, and sidecar output with the fake client.

If you want to run the reviewed flow manually with live calls:

1. run stage 1 with `--wait`
2. create reviewer notes and an approved review bundle
3. rerun stage 2 with `--review-bundle`
4. repeat the bundle step for stage 2
5. rerun stage 3 with the second review bundle

## When To Use This Pack

Use the synthetic pack when you want to verify:

- the CLI can dry-run a pack cleanly
- review gates stop progression until an approved bundle is supplied
- sidecar extraction is working
- the run directory structure is being written correctly

Do not use the synthetic pack as production content. Copy its structure, then replace the prompts, manifests, schemas, and corpus with task-specific assets.
