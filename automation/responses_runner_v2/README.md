# Responses Runner V2

`responses_runner_v2` is the engine package behind `staged-workflow-runner`. It loads a task
pack, builds one Responses API request per stage, submits it in background mode, waits, writes
`artifact.md`, applies the stage gate, and records everything in a single `run_manifest.json`.

## Entry Points

- Runner CLI: `automation/run_responses_v2.py` (`run`, `resume`, `refresh`, `cancel`,
  `recover-uploads`)
- Eval CLI: `automation/run_responses_v2_eval.py`
- Operator runbook: `docs/runbooks/responses-runner-v2.md`

## Operating Model

- Each invocation operates against **one exact workspace root**, resolved in this order:
  explicit `--root`, then `RESPONSES_RUNNER_V2_ROOT`, then the current working directory.
- There is no repo-marker search and no fallback to the runner module location.
- Workflow manifests, static attachments, handoff notes, carry-forward artifacts, and run
  outputs must all stay under that root. Task packs can live anywhere under it because asset
  references resolve relative to the workflow manifest.

## What Happens Per Stage

1. `pack_loader.py` loads and validates the workflow manifest and the stage input manifest.
2. `attachments.py` resolves files and directories into attachments, wraps unsupported text as
   markdown, enforces byte limits, uploads files, and renders `input_manifest.md`.
3. `workflow.py` builds `request_payload.json`, runs the exact token preflight
   (`POST /responses/input_tokens`; blocks when the count exceeds the stage
   `max_input_tokens`; `--skip-token-count` disables it), submits the background response, and
   polls until it reaches a terminal status.
4. `artifacts.py` writes `response.final.json`, `artifact.md`, and, when the stage declares a
   structured output schema, `output.structured.json`. `validators.py` runs any configured
   post-output validators and records the result; validators are advisory and never block.
5. The stage gate is applied: `auto` continues, `reviewed` calls `reviewer.py`, `human` stops
   the run until `--handoff-note` is supplied, `terminal` ends the run.
6. Every transition rewrites `run_manifest.json` atomically under the run lock.

## Authority Model

Every stage uses the same attachment authority order:

1. Primary Job Inputs
2. Reviewed Handoff Inputs
3. Attached Workspace Evidence (the manifest field remains `attached_repository_files`)
4. Reference Context

The stage-local `input_manifest.md` is the human-readable source of truth for what was attached.

## Modules

- `contracts.py`: constants, schema versions, authority roles, gate and status enums, allowed
  stage transitions, `ReviewConfig`, `RuntimeOptions`, model caps, assurance profiles.
- `pack_loader.py`: loads and validates workflow manifests, input manifests, tool profiles, and
  schema references. Accepts the legacy gate spelling `review_required` as `human` and the
  legacy `review_bundle_from_stage_id` as an alias for `handoff_from_stage_id`.
- `workflow.py`: orchestration. Picks the next eligible stage, resolves operator inputs and
  handoffs, builds and submits requests, waits, resumes, refreshes, cancels, finalizes, and
  applies gates.
- `reviewer.py`: builds the review job for a `reviewed` stage, invokes one reviewer CLI
  (`codex` or `claude`), extracts and normalizes the verdict, and writes the review evidence.
- `attachments.py`: attachment resolution, wrapping, byte limits, uploads, manifest rendering.
- `openai_client.py`: thin urllib client for `/responses`, `/responses/input_tokens`, `/files`.
- `artifacts.py`: on-disk run layout, run-manifest load/save, response and artifact writers.
- `validators.py`: advisory post-output validators (for example `evidence_references_v1`).
- `schema_validation.py`: JSON Schema validation for manifests and verdicts.
- `locking.py`: the per-run `.runner.lock`.
- `prompts/stage_review.md`: the reviewer prompt.
- `schemas/`: `workflow_manifest.v2`, `input_manifest`, `runtime_input_bindings`,
  `run_manifest.v2`, `stage_review_verdict`, `validator_result`.

## Task-Pack Contract

A task pack normally includes one workflow manifest, one static input manifest per stage, one
shared instructions file, one task prompt per stage, zero or one tool profile per stage, and
optional output schema files:

```text
<task_pack>/
  shared_instructions.md
  prompts/
    stage1_task.md
    stage2_task.md
  inputs/
    stage1.input_manifest.json
    stage2.input_manifest.json
  workflows/
    <workflow>.workflow.json
  tools/
    web_search.profile.json
```

The workflow manifest (`schemas/workflow_manifest.v2.schema.json`) declares `workflow_id`,
`workflow_mode`, `assurance_profile`, `shared_instructions_file`, `operator_requirements`,
`defaults` (model roles, request defaults, `review`), and the ordered `stages`. Each stage
declares `stage_id`, `stage_number`, `title`, `task_file`, `input_manifest_file`, `model_role`,
`max_input_tokens`, `gate`, `output`, and optionally `carry_forward`
(`handoff_from_stage_id`, `reference_context_from_stage_ids`), `review`, `post_output_validators`,
`citation_policy`, `tool_profile_file`, `reasoning_effort`, `verbosity`, `max_output_tokens`.

Review settings (`defaults.review` and per-stage `review`): `reviewer` (`codex` default,
`claude`, or `none`), `model` (default `gpt-5.6-sol` for codex, `opus` for claude), `effort`
(default `high` for codex, `xhigh` for claude), `timeout_seconds` (1800), `max_revisions` (1).

The stage input manifest (`schemas/input_manifest.schema.json`) lists the static attachments per
authority role. At runtime the engine merges it with operator `--primary-job-input` and
`--reference-context` paths, `--input-binding-file` bindings scoped to the stage, and
carry-forward artifacts and notes from earlier stages.

## Run Layout

Default output root: `.local/automation/responses_runner_v2/runs`.

```text
<run_dir>/
  run_manifest.json          the only durable record for the run
  .runner.lock
  stages/<NN_stage_id>/attempt_NNN/
    input_manifest.json
    input_manifest.md
    request_payload.json
    token_preflight.json | token_preflight.error.json
    uploads.json
    response.latest.json
    response.final.json
    artifact.md
    validator_report.json    when validators are configured
    output.structured.json   when a structured output schema is configured
    review/                  reviewed gates only
      verdict.json
      reviewer_notes.md
      prompt_<stamp>.md, stdout_<stamp>.txt, stderr_<stamp>.txt, invocation_<stamp>.json
  dry_runs/                  --dry-run renders
    stages/<NN_stage_id>/{input_manifest.json,input_manifest.md,request_payload.json,upload_inputs/}
    stubs/<stage_id>/{artifact.md,handoff_notes.md}
```

`run_manifest.json` (`schemas/run_manifest.v2.schema.json`) records run status, stage order,
the operator overrides the run was started with (so `resume` rebuilds the same runtime
options), and per stage: status, attempts (with `response_id`, `request_sha256`,
`request_wall_ms`, and `revision_of_attempt_id` for revisions), artifact paths and hashes, the
token preflight result, validator outcome, `review_status`, and the paths of the verdict,
reviewer notes, and human handoff note. A revision attempt of the same stage lands in the next
`attempt_NNN` directory. Timestamps are ISO 8601 in UTC.

`artifact.md` is the clean deliverable; `response.final.json` is raw recovery evidence.

## Examples

- `automation/examples/responses_runner_v2_synthetic/`: bounded proof pack (`one_pass`,
  `two_pass`, `reviewed_three_stage` with human gates and handoff notes).
- `automation/examples/responses_runner_v2_evidence_synthesis/`: offline evidence and citation
  example with human gates and the evidence-reference validator.
- `automation/task_packs/gstack_design_to_po_playbook/`: the real five-stage lane with
  `reviewed` gates on stages 1-4 and a `terminal` stage 5.

## Recommended Reading Order

1. `docs/runbooks/responses-runner-v2.md`
2. `automation/run_responses_v2.py`
3. `automation/responses_runner_v2/contracts.py`
4. `automation/responses_runner_v2/pack_loader.py`
5. `automation/responses_runner_v2/workflow.py`
6. `automation/responses_runner_v2/reviewer.py`
7. `automation/responses_runner_v2/attachments.py`
8. `automation/tests/test_responses_runner_v2_reviewed_gates.py`
