# Responses Runner V2

This runbook covers day-to-day operation of the runner inside the `staged-workflow-runner` repository while the first release preserves the existing `responses_runner_v2` package and CLI names.

It also covers the additive supervisor lane for end-to-end AI-operated execution after an initial clarification gate.

## Prerequisites

- Python 3
- `OPENAI_API_KEY` set in the environment, or a `.env` file in the workspace root used for the run
- a workflow manifest and all statically referenced assets stored under one workspace root
- for the supervisor review lane:
  - Codex CLI available as `codex`
  - Claude Code CLI available as `claude`
  - non-interactive execution allowed in the current shell

## Current Operator Default

Until token-preflight service reliability is confirmed for the deployment environment, live workflow submissions may use `--skip-token-count` only when an operator explicitly accepts that operational tradeoff.

This is an operational default, not a runner-engine requirement.

## Workspace Root Contract

The first release uses **one exact workspace root per run and supervisor session**.

Resolution order:

1. explicit CLI `--root`
2. `RESPONSES_RUNNER_V2_ROOT`
3. current working directory as-is

Implications:

- `--workflow-file` is resolved under that workspace root
- `--primary-job-input`, `--review-bundle`, `--run-dir`, and `--output-root` must stay under that same root
- supervisor sessions are written under `.local/automation/responses_runner_v2/supervisor_sessions/`
- review bundles and carry-forward artifacts are validated under the same root
- there is no dual-root mode in this release

## Model Defaults

Use:

- primary generation: `gpt-5.5-pro`
- structural processing: `gpt-5.5`
- committed GPT-5.5-family prompt cache retention: `24h`
- locked high-stakes self-improvement max output: `128000`

The workflow loader rejects GPT-5.5-family committed profiles that omit `24h` or use stale in-memory cache posture.

## Generic Runner Smoke Test

From the repository root:

```bash
python automation/run_responses_v2.py run \
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
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --skip-token-count \
  --wait
```

## External-Project Invocation

If the runner checkout is separate from the target project:

```bash
python /path/to/staged-workflow-runner/automation/run_responses_v2.py run \
  --root /path/to/target-workspace \
  --workflow-file task_packs/example/workflows/example.workflow.json \
  --primary-job-input docs/approved_brief.md \
  --skip-token-count \
  --wait
```

Important:

- `--workflow-file` is interpreted under `/path/to/target-workspace`, not relative to the runner checkout
- the task pack must live under the same target workspace root in first release
- the target workspace root also becomes the base for `.env` lookup when the CLI creates the OpenAI client

## Review-Required Stages With Generic Runner

When a workflow stage requires review:

1. run the stage
2. inspect generated artifacts under the run directory
3. create reviewer notes
4. create an approved review bundle
5. rerun the workflow with `--review-bundle`

Bundle creation example:

```bash
python automation/create_review_bundle_v2.py \
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
python automation/run_responses_v2.py run \
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
python automation/run_responses_v2.py resume \
  --root . \
  --run-dir <run_dir> \
  --stage <stage_id> \
  --wait
```

Refresh remote status without resubmitting work:

```bash
python automation/run_responses_v2.py refresh \
  --root . \
  --run-dir <run_dir> \
  --stage <stage_id>
```

Use `refresh` when you only want the latest remote status recorded locally. Use `resume` when you want the runner to continue through terminal completion and artifact finalization.

If a remote stage is already terminal but you are missing `response.final.md`, `output.structured.json`, or `sidecar.response.*`, that is a local finalization gap. Use `resume` on the stage to write final artifacts. `refresh` is status-only and will not backfill them.

## Supervisor Lane Overview

The supervisor lane automates the future normal path after an initial human clarification gate.

Mandatory human participation:

1. human supplies task;
2. operator asks clarification questions if needed;
3. human accepts a clarified task brief.

After that, normal execution is AI-operated.

The supervisor review sequence for every scaffold and non-terminal stage is:

1. operator Codex prepares provisional review and bundle;
2. Codex review agent independently reviews via `codex exec`;
3. Claude review agent independently reviews via `claude --bare -p`;
4. deterministic consolidation merges findings;
5. operator Codex accepts only supported recommendations with applied-change evidence;
6. supervisor creates the approved bundle or blocks progression.

## Supervisor Commands

Initialize a session:

```bash
python automation/run_responses_supervisor_v2.py init-session \
  --root . \
  --clarified-task-brief docs/clarified_task_brief.md \
  --summary "Accepted task summary"
```

Stage a scaffold:

```bash
python automation/run_responses_supervisor_v2.py stage-scaffold \
  --root . \
  --session <session_id> \
  --scaffold-path automation/task_packs/<task_pack>
```

Dry-run a scaffold:

```bash
python automation/run_responses_supervisor_v2.py dry-run-scaffold \
  --root . \
  --session <session_id> \
  --workflow-file automation/task_packs/<task_pack>/workflows/<workflow>.json
```

Invoke operator Codex provisional review:

```bash
python automation/run_responses_supervisor_v2.py invoke-operator \
  --root . \
  --session <session_id> \
  --review-cycle <cycle_id> \
  --review-kind scaffold \
  --job-json <session_dir>/review_cycles/<cycle_id>/operator_job.json
```

Invoke independent reviewers:

```bash
python automation/run_responses_supervisor_v2.py invoke-reviewers \
  --root . \
  --session <session_id> \
  --review-cycle <cycle_id> \
  --review-kind scaffold \
  --job-json <session_dir>/review_cycles/<cycle_id>/review_job.json
```

Consolidate:

```bash
python automation/run_responses_supervisor_v2.py consolidate \
  --root . \
  --session <session_id> \
  --review-cycle <cycle_id> \
  --codex-review <path/to/codex_review.json> \
  --claude-review <path/to/claude_review.json> \
  --operator-review <path/to/operator_provisional.json> \
  --output <path/to/consolidated_review.json>
```

Create operator acceptance:

```bash
python automation/run_responses_supervisor_v2.py accept \
  --root . \
  --session <session_id> \
  --review-cycle <cycle_id> \
  --consolidated-review <path/to/consolidated_review.json> \
  --accept-recommendation <recommendation_id> \
  --applied-change-evidence <path/to/applied_change_evidence.json> \
  --output <path/to/operator_acceptance.json>
```

Classify stage outcome:

```bash
python automation/run_responses_supervisor_v2.py classify \
  --root . \
  --session <session_id> \
  --run-dir <run_dir> \
  --stage <stage_id>
```

Archive a failed no-artifact attempt before rerun:

```bash
python automation/run_responses_supervisor_v2.py archive-attempt \
  --root . \
  --session <session_id> \
  --run-dir <run_dir> \
  --stage <stage_id> \
  --reason failed_no_artifact
```

Validate a session:

```bash
python automation/run_responses_supervisor_v2.py validate-session \
  --root . \
  --session <session_id>
```

## Review-Agent Command Contracts

Canonical operator and Codex review command shape:

```bash
codex exec "<prompt_plus_job_json>"
```

Canonical Claude review command shape:

```bash
claude --bare -p \
  --model opus \
  --effort max \
  --output-format json \
  --append-system-prompt-file automation/task_packs/responses_runner_v2_supervisor_internal/prompts/claude_review.md \
  "<review_job_json>"
```

If `--effort max` is unsupported locally, the supervisor retries once with `--effort xhigh` and records `fallback_used=true`.

Agent JSON transport:

- JSON is read from stdout;
- stdout and stderr are always captured;
- supervisor writes validated JSON and markdown sidecars;
- missing, malformed, or schema-invalid JSON is a hard failure.

Read-only review enforcement:

- reviewer commands are not allowed to edit source files;
- supervisor snapshots workspace source files before and after reviewer commands;
- unexpected modifications produce `read_only_violation`.

## Failure And Recovery Policy

| case | action |
|---|---|
| `completed_complete_artifact` | Review through normal operator/Codex/Claude/consolidation/acceptance loop. |
| `failed_complete_artifact` | Treat as reviewable; preserve failed status; review and bundle only after acceptance. |
| `failed_no_artifact` | No bundle; archive attempt with request/scaffold hashes; rerun as-is only if retry budget and unchanged-input evidence allow. |
| `incomplete_output_limit` | No auto-progress; create human-pause artifact and recovery plan. |
| `blocked_token_preflight` | No live submission and no bundle; repair scaffold/input set before relaunch. |
| `long_running_monitoring_anomaly` | Refresh/resume existing `response_id`; never duplicate-submit while it may complete. |

## Human Pause Conditions

Post-clarification human pauses are exception paths only. A pause artifact must state:

- trigger;
- artifact to present;
- decision required;
- safe continuation action;
- whether automation may resume;
- whether review-bundle creation is blocked.

Typical triggers:

- output-limit incomplete requiring scope/model/budget decision;
- blocked preflight where context reduction changes high-authority scope;
- repeated monitoring anomaly after configured thresholds;
- missing required review-agent CLI with no approved fallback;
- unresolved evidence conflict that the operator cannot resolve.

## Validation

Run focused supervisor and migration tests:

```bash
python -m pytest \
  automation/tests/test_responses_runner_v2_model_migration.py \
  automation/tests/test_responses_runner_v2_supervisor.py
```

Run broader runner regression tests:

```bash
python -m pytest \
  automation/tests/test_responses_runner_v2_contracts.py \
  automation/tests/test_responses_runner_v2_workflow.py \
  automation/tests/test_responses_runner_v2_review_bundle.py
```

## Rollout Sequence

1. Apply the drop-in packet.
2. Run model migration tests.
3. Run supervisor tests.
4. Run existing contract/workflow/review-bundle tests.
5. Dry-run the synthetic one-pass workflow.
6. Dry-run the supervised end-to-end self-improvement workflow.
7. Only then launch live supervised execution.
