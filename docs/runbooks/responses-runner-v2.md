# Responses Runner V2

This runbook covers day-to-day operation of the runner inside the `staged-workflow-runner` repository while the first release preserves the existing `responses_runner_v2` package and CLI names.

## Prerequisites

- Python 3
- `OPENAI_API_KEY` set in the environment, or a `.env` file in the workspace root used for the run
- a workflow manifest and all statically referenced assets stored under one workspace root

## Current Operator Default

Until the server-side `/responses/input_tokens` issue is resolved, run live workflow submissions with `--skip-token-count`.

This is an operational default, not a runner-engine requirement.

## Workspace Root Contract

The first release uses **one exact workspace root per run**.

Resolution order:

1. explicit CLI `--root`
2. `RESPONSES_RUNNER_V2_ROOT`
3. current working directory as-is

Implications:

- `--workflow-file` is resolved under that workspace root
- `--primary-job-input`, `--review-bundle`, `--run-dir`, and `--output-root` must also stay under that same root
- review bundles and carry-forward artifacts are validated under that same root
- there is no dual-root mode in this release

If the runner checkout is separate from the target project, invoke the script from this repository but point `--root` at the target project root.

## Same-Repo Smoke Test

From the repository root:

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --dry-run
```

That command should create a run directory under `.local/automation/responses_runner_v2/runs/` and write, at minimum:

- `run_manifest.json`
- `stages/01_draft_summary/input_manifest.json`
- `stages/01_draft_summary/request_payload.json`
- `stages/01_draft_summary/stage_checkpoint.json`

## Live Run

To submit the same synthetic workflow live and wait for completion:

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --skip-token-count \
  --wait
```

## External-Project Invocation

If the runner checkout is separate from the target project:

```bash
python3 /path/to/staged-workflow-runner/automation/run_responses_v2.py run \
  --root /path/to/target-workspace \
  --workflow-file task_packs/example/workflows/example.workflow.json \
  --primary-job-input docs/approved_brief.md \
  --skip-token-count \
  --wait
```

Important:

- `--workflow-file` is interpreted under `/path/to/target-workspace`, **not** relative to the runner checkout
- the task pack must live under the same target workspace root in first release
- the target workspace root also becomes the base for `.env` lookup when the CLI creates the OpenAI client

## Review-Required Stages

When a workflow stage requires review:

1. run the stage
2. inspect the generated artifacts under the run directory
3. create reviewer notes
4. create an approved review bundle
5. rerun the workflow with `--review-bundle`

Bundle creation example:

```bash
python3 automation/create_review_bundle_v2.py \
  --root . \
  --output review_bundle.json \
  --workflow-id synthetic_reviewed_three_stage \
  --source-stage-id proposal \
  --source-run-id <run_id> \
  --primary-artifact-markdown <run_dir>/stages/01_proposal/response.final.md \
  --response-artifact-json <run_dir>/stages/01_proposal/response.final.json \
  --reviewer-notes notes.md
```

Then continue:

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/reviewed_three_stage.workflow.json \
  --run-dir <run_dir> \
  --review-bundle review_bundle.json \
  --skip-token-count \
  --wait
```

## Resume And Refresh

Resume a nonterminal stage:

```bash
python3 automation/run_responses_v2.py resume \
  --root . \
  --run-dir <run_dir> \
  --stage <stage_id> \
  --wait
```

Refresh remote status without resubmitting work:

```bash
python3 automation/run_responses_v2.py refresh \
  --root . \
  --run-dir <run_dir> \
  --stage <stage_id>
```

Use `refresh` when you only want the latest remote status recorded locally. Use `resume` when you want the runner to continue through terminal completion and artifact finalization.

If a remote stage is already terminal but you are missing `response.final.md`, `output.structured.json`, or `sidecar.response.*`, that is a local finalization gap, not a sidecar-schema failure. Use `resume` on the stage to write the final artifacts. `refresh` is status-only and will not backfill them.

## Eval Harness

List bundled eval cases:

```bash
python3 automation/run_responses_v2_eval.py \
  --dataset-file automation/evals/responses_runner_v2.eval.json \
  --list-cases
```

Score a JSON artifact:

```bash
python3 automation/run_responses_v2_eval.py \
  --dataset-file automation/evals/responses_runner_v2.eval.json \
  --case-id run-manifest-contract \
  --artifact <run_dir>/run_manifest.json
```

## Guardrails

- Use `--dry-run` before the first live submission of a new task pack.
- Use `--skip-token-count` for live `run` commands until the server-side token-preflight issue is resolved.
- Keep committed task-pack manifests free of `.local/...` snapshot paths.
- Keep all statically referenced files under the workspace root.
- Packs may live anywhere under the workspace root; there is no required `task_packs/` directory.
- If you need a runner-root / target-root split, treat that as a later extension rather than patching it into first-release workflows.
