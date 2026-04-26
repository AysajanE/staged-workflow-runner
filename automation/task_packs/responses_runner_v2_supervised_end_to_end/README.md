# Responses Runner V2 Supervised End-To-End Self-Improvement Pack

This task pack supersedes the older `responses_runner_v2_supervisory_lane` three-stage scaffold.

Its purpose is to run the current staged workflow runner on the task of designing the next-generation automated supervisory lane for itself.

The final stage must output a complete drop-in-ready implementation packet that the team can apply directly to this repository.

## Why This Pack Uses Four Stages

The new requirements add a second high-stakes design axis: independent non-interactive review by:

- a Codex review agent via `codex exec`
- a Claude review agent via `claude -p`
- an operator Codex agent that consolidates recommendations and accepts only supported changes

That review-and-agent protocol is too important to bury inside a generic draft stage. The workflow therefore has four material stages:

1. `architecture_and_supervision_protocol`
2. `agent_review_protocol_and_package_contract`
3. `draft_drop_in_packet`
4. `final_drop_in_packet`

Each stage performs a non-trivial, high-stakes task.

## Stage Summary

### Stage 1 — Architecture And Supervision Protocol

Locks:

- overall supervisory architecture
- operator/reviewer/consolidator topology
- failure and recovery model
- model migration posture from GPT-5.4 to GPT-5.5
- minimum-change integration boundary
- human-pause conditions

### Stage 2 — Agent Review Protocol And Package Contract

Locks:

- exact Codex operator prompt contract
- exact Codex review-agent prompt contract
- exact Claude review-agent prompt contract
- command and artifact protocol for `codex exec` and `claude -p`
- review consolidation protocol
- implementation file inventory
- schema and validation baseline

### Stage 3 — Draft Drop-In Packet

Produces a complete draft package, with full contents or exact patches for all files.

The draft must be good enough for an independent two-agent review loop.

### Stage 4 — Final Drop-In Packet

Hardens the draft after review and emits the final package.

No downstream design work should be required after this stage.

## Model Configuration

This pack uses:

- primary generation: `gpt-5.5-pro`
- structural processing: `gpt-5.5`

The final implementation packet must update the repository's own defaults, model caps, example workflows, task packs, docs, and tests away from `gpt-5.4` / `gpt-5.4-pro` where appropriate.

## Recommended Dry Run

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json \
  --dry-run
```

## Recommended Live Stage 1 Run

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json \
  --skip-token-count \
  --wait
```

## Review-Bundle Pattern

After each review-required stage, create reviewer notes and an approved review bundle with:

```bash
python3 automation/create_review_bundle_v2.py \
  --root . \
  --output <run_dir>/<stage_id>.review_bundle.json \
  --workflow-id responses_runner_v2_supervised_end_to_end_self_improvement \
  --source-stage-id <stage_id> \
  --source-run-id <run_id> \
  --primary-artifact-markdown <run_dir>/stages/<NN_stage_id>/response.final.md \
  --response-artifact-json <run_dir>/stages/<NN_stage_id>/response.final.json \
  --reviewer-notes <run_dir>/<stage_id>.reviewer_notes.md
```

Then continue the run with:

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json \
  --run-dir <run_dir> \
  --review-bundle <run_dir>/<stage_id>.review_bundle.json \
  --skip-token-count \
  --wait
```

## Manual Review For This Meta-Run

This current meta-run is still reviewed manually between stages.

The future lane produced by this workflow must automate those reviews with the operator Codex agent, Codex review agent, and Claude review agent.
