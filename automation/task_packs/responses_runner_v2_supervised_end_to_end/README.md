# Responses Runner V2 Supervised End-To-End Self-Improvement Pack

This task pack supersedes the older `responses_runner_v2_supervisory_lane` three-stage scaffold.

Its purpose is to run the staged workflow runner on the task of designing the next-generation automated supervisory lane for itself.

The final stage must output a complete drop-in-ready implementation packet that the team can apply directly to this repository.

## Why This Pack Uses Four Stages

The requirements add a second high-stakes design axis: independent non-interactive review by:

- a Codex review agent via `codex exec`
- a Claude review agent via subscription-authenticated `claude -p`
- an operator Codex agent that consolidates recommendations and accepts only supported changes

That review-and-agent protocol is too important to bury inside a generic draft stage. The workflow therefore has four material stages:

1. `architecture_and_supervision_protocol`
2. `agent_review_protocol_and_package_contract`
3. `draft_drop_in_packet`
4. `final_drop_in_packet`

Each stage performs a non-trivial, high-stakes task.

## Stage Boundary

- Stage 1 locks architecture and supervision protocol.
- Stage 2 locks agent review protocol, command contracts, prompt contracts, file inventory, and implementation contracts.
- Stage 3 emits a complete draft drop-in packet.
- Stage 4 emits the final hardened drop-in packet.

Stage 3 and Stage 4 must not reopen the architecture, engine/supervisor boundary, agent topology, consolidation-vs-acceptance separation, failure policy, human-pause contract, or model posture unless a concrete repository contradiction is discovered and recorded for review.

## Review-Agent Topology

The future lane produced by this workflow must include:

- accountable operator Codex lane;
- independent read-only Codex review-agent lane;
- independent read-only Claude review-agent lane;
- deterministic consolidation pass;
- separate operator selective-acceptance artifact.

For every non-terminal stage:

1. operator Codex prepares provisional review and next-stage bundle;
2. Codex review agent independently reviews;
3. Claude review agent independently reviews;
4. consolidation preserves provenance and classifies recommendations;
5. operator Codex accepts only evidence-supported recommendations with applied-change evidence;
6. supervisor creates the approved review bundle or blocks progression.

## Model Configuration

This pack uses:

- primary generation: `gpt-5.5-pro`
- structural processing: `gpt-5.5`
- prompt cache retention: `24h`
- max output tokens for all stages: `128000`

The final implementation packet updates the repository's own defaults, model caps, example workflows, task packs, docs, and tests to the same GPT-5.5-family posture.

## Current Four-Stage Tool Posture

The current successful `draft_drop_in_packet` Stage 3 uses no `tool_profile_file` in `workflows/four_stage.workflow.json`.

Do not reintroduce Stage 3 web search for this current four-stage workflow unless a later approved handoff provides concrete safety evidence and explicitly reopens this decision.

## Failure Policies Locked By This Pack

The implementation must distinguish:

- `completed_complete_artifact`
- `failed_complete_artifact`
- `failed_no_artifact`
- `incomplete_output_limit`
- `blocked_token_preflight`
- `long_running_monitoring_anomaly`

A failed stage with a complete substantive artifact is reviewable. A failed stage without a substantive artifact may be rerun as-is only after archive-before-rerun evidence. Output-limit incomplete outcomes must not auto-progress.

## Recommended Dry Run

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json \
  --dry-run
```

## Recommended Live Stage 1 Run

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json \
  --skip-token-count \
  --wait
```

## Manual Review For This Meta-Run

This current meta-run is still manually reviewed between stages.

The future lane produced by this workflow automates those reviews with:

- operator Codex;
- Codex review agent;
- Claude review agent;
- consolidation;
- operator selective acceptance.

## Review-Bundle Pattern For This Meta-Run

After each review-required stage in this current manual meta-run, create reviewer notes and an approved review bundle with:

```bash
python automation/create_review_bundle_v2.py \
  --root . \
  --output <run_dir>/<stage_id>.review_bundle.json \
  --workflow-id responses_runner_v2_supervised_end_to_end_self_improvement \
  --source-stage-id <stage_id> \
  --source-run-id <run_id> \
  --primary-artifact-markdown <run_dir>/stages/<NN_stage_id>/response.final.md \
  --response-artifact-json <run_dir>/stages/<NN_stage_id>/response.final.json \
  --reviewer-notes <run_dir>/<stage_id>.reviewer_notes.md
```

Then continue:

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json \
  --run-dir <run_dir> \
  --review-bundle <run_dir>/<stage_id>.review_bundle.json \
  --skip-token-count \
  --wait
```

## Validation Focus

Stage reviewers should inspect:

- command contracts for `codex exec` and subscription-authenticated `claude -p`;
- reviewer read-only enforcement;
- JSON stdout transport and schema validation;
- consolidation separated from operator acceptance;
- no blind reviewer acceptance;
- accepted recommendations requiring applied-change evidence;
- GPT-5.5 workflow posture with explicit `24h`;
- current Stage 3 no-tools posture;
- failure policy tests;
- human-pause artifact completeness;
- final inventory/file-block parity.
