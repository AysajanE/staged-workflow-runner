# Two-Tier Workspace Onboarding And Deployment

This runbook explains the two-tier operating model for deploying `staged-workflow-runner` into an arbitrary target project workspace.

Use this when:

- a new team needs to learn the runner
- an existing runner-trained team needs to launch the runner in a new project workspace
- you want a repeatable path from runner onboarding to first live run

This document keeps the walkthrough in the runner repository so it can be reused across projects.

## Purpose

The core idea is to avoid reteaching the full runner architecture every time a new project appears.

The model has two layers:

1. one-time runner onboarding
2. per-workspace launch briefing

Tier 1 is reused across projects.
Tier 2 is recreated for each target workspace.

## Fixed Runner Path

In this local environment, the runner repository is:

- `<runner-checkout>`

## Tier 1: One-Time Runner Onboarding

Tier 1 is for a person who has not operated this runner before.

They should complete this once before running high-stakes workflows in any target workspace.

### Step 1: Read The Core Runner Material

Read these files in order:

1. `<runner-checkout>/TEAM_ONBOARDING.md`
2. `<runner-checkout>/docs/runbooks/responses-runner-v2.md`
3. `<runner-checkout>/docs/runbooks/first-use-adaptation-example.md`
4. `<runner-checkout>/automation/examples/responses_runner_v2_synthetic/README.md`

If deeper execution-path understanding is needed, then read:

5. `<runner-checkout>/automation/responses_runner_v2/workflow.py`

### Step 2: Understand The Non-Negotiable Runner Facts

Before moving on, the operator should be clear on these facts:

- The runner engine is generic.
- Task behavior lives in workflow manifests, prompts, input manifests, tool profiles, and optional schemas.
- The first release uses one exact workspace root per run.
- All statically referenced workflow assets, review bundles, and run artifacts must stay under that same workspace root.
- Stage progression is sequential and gate-driven.
- Attachment authority order is fixed:
  1. Primary Job Inputs
  2. Reviewed Handoff Inputs
  3. Attached Repository Files
  4. Reference Context
- Dry run, resume, refresh, review bundles, and optional sidecar extraction are built into the runner.
- Live `run` commands should use `--skip-token-count`.

### Step 3: Validate The Runner With The Synthetic Proof Pack

This is the proof path for learning the runner without touching a real project.

Run the example-pack unit test:

```bash
cd <runner-checkout>

python3 -m unittest automation.tests.test_responses_runner_v2_example_pack
```

Dry-run the synthetic one-pass workflow:

```bash
cd <runner-checkout>

python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --dry-run
```

What to inspect:

- `.local/automation/responses_runner_v2/runs/<run-id>/run_manifest.json`
- `.local/automation/responses_runner_v2/runs/<run-id>/stages/01_draft_summary/input_manifest.json`
- `.local/automation/responses_runner_v2/runs/<run-id>/stages/01_draft_summary/input_manifest.md`
- `.local/automation/responses_runner_v2/runs/<run-id>/stages/01_draft_summary/request_payload.json`
- `.local/automation/responses_runner_v2/runs/<run-id>/stages/01_draft_summary/stage_checkpoint.json`

Tier 1 is complete when the operator understands where artifacts go, how a task pack is wired, and what a correct dry run looks like.

## Tier 2: Per-Workspace Launch Brief

Tier 2 is for a specific target project workspace.

It assumes the operator already understands the runner and only needs the project-specific facts.

The output of Tier 2 is a short workspace-local brief, usually named `RUNNER_PLAYBOOK.md`, plus a task pack under the target workspace root.

### What Belongs In Tier 2

The per-workspace brief should include:

- the exact target workspace root
- the task objective
- the task-pack path
- the workflow path
- the canonical source files
- the exact dry-run command
- the exact live-run command
- the artifact locations to inspect
- any workspace-specific caveats

### What Does Not Belong In Tier 2

The per-workspace brief should not try to reteach:

- the full runner architecture
- the full internal module map
- the synthetic example details
- the general runner design rationale

That material stays centralized in Tier 1 documents.

## Standard Two-Tier Workflow

For a new target workspace:

1. Decide whether the team already has a runner-trained operator.
2. If not, have one operator complete Tier 1.
3. Copy the short workspace launch-brief template into the target workspace.
4. Fill in the project-specific details.
5. Define the first bounded task.
6. Create the task pack under the target workspace root.
7. Dry-run the task pack from the target workspace.
8. Inspect the generated request and manifests.
9. Launch the first live run from the target workspace with `--skip-token-count --wait`.

## Example Walkthrough

This section walks through the model for this concrete target workspace:

- `<target-workspace>`

The goal here is not to define the task itself yet.
The goal is to make the team in that target project operationally ready.

### Step 1: Confirm The Target Workspace Exists

The target workspace root is:

- `<target-workspace>`

This must be the root used for task-pack assets, run artifacts, and `.env` lookup.

### Step 2: Decide Whether The Team Needs Tier 1

Ask one simple question:

- Does at least one operator on that team already know this runner and understand the single-root model, dry-run flow, and review-bundle flow?

If the answer is no:

- have that operator complete Tier 1 first

If the answer is yes:

- skip directly to Step 3

### Step 3: Copy The Workspace Brief Template

Copy the short template from the runner repo into the target workspace:

```bash
cp \
  "<runner-checkout>/docs/runbooks/project-workspace-playbook-template.md" \
  "<target-workspace>/RUNNER_PLAYBOOK.md"
```

That copied file becomes the workspace-local operator brief.

### Step 4: Fill In The Workspace Brief

Open:

- `<target-workspace>/RUNNER_PLAYBOOK.md`

Fill in:

- target workspace root
- current task objective
- task-pack root
- workflow file
- expected primary artifact
- canonical source files
- current caveats

The point is to make the file answer the practical questions an operator has when standing inside that workspace.

### Step 5: Choose The First Task

Do not start with a large or ambiguous workflow.

For a new workspace, the first task should be:

- one bounded task
- one clear deliverable
- one-pass if possible
- cheap to review manually
- safe to rerun

Avoid starting with a reviewed multi-stage high-complexity pack unless the team already has experience with this runner in real work.

### Step 6: Identify The Canonical Source Files

All files that will be attached statically must live under:

- `<target-workspace>`

Based on the current layout, likely starting points include:

- `<target-workspace>/README.md`
- `<target-workspace>/contracts/project.yaml`
- `<target-workspace>/contracts/model_spec.md`
- `<target-workspace>/docs/protocol.md`
- `<target-workspace>/docs/autonomous_agentic_research_workflow_roadmap.md`

If the task depends on specific data or processed outputs, include the relevant files under:

- `<target-workspace>/data`

### Step 7: Classify Inputs By Authority

Before writing the input manifest, decide which files belong in which authority bucket:

- `primary_job_inputs`
  Directly define the immediate task and should outrank everything else.
- `attached_repository_files`
  Supporting repo-local evidence.
- `reference_context`
  Lower-authority context that should not override stronger inputs.
- `reviewed_handoff_inputs`
  Approved artifacts from earlier gated stages.

Do this classification deliberately.
Do not dump everything into one bucket.

### Step 8: Create The Task-Pack Skeleton

Create the initial task pack under the target workspace root.

Recommended starting layout:

```text
<target-workspace>/
  task_packs/
    <task-name>/
      shared_instructions.md
      prompts/
        stage1.md
      inputs/
        stage1.input_manifest.json
      workflows/
        <workflow-id>.workflow.json
      tools/
        no_tools.profile.json
```

Start with `no_tools` unless tools are truly required.

### Step 9: Write The Shared Instructions

Write `shared_instructions.md` for the new task pack.

It should contain:

- one or two model roles
- the cross-stage goal
- the attachment authority order
- grounding rules
- a short output contract

Do not overload this file with every stage requirement.

### Step 10: Write The Stage Prompt

Write the first stage prompt.

This file is the real task contract.

It should specify:

- the stage objective
- the required output shape
- what the model must decide
- how grounded claims should be cited
- how inferences should be labeled

Start with one stage unless there is a strong reason not to.

### Step 11: Write The Input Manifest

Write `inputs/stage1.input_manifest.json`.

Rules:

- every static path must resolve under `<target-workspace>`
- only task-defining inputs go into `primary_job_inputs`
- supporting repo evidence goes into `attached_repository_files`
- lower-authority context goes into `reference_context`

### Step 12: Write The Workflow Manifest

Write `workflows/<workflow-id>.workflow.json`.

For the first task in a new workspace, keep it simple:

- `one_pass`
- one stage
- `no_tools`
- no `max_input_tokens`
- one primary text output
- default model posture unless the task has a clear reason to change it

### Step 13: Dry-Run From The Target Workspace

Run the dry run from inside the target workspace:

```bash
cd "<target-workspace>"

python3 "<runner-checkout>/automation/run_responses_v2.py" run \
  --root . \
  --workflow-file task_packs/<task-name>/workflows/<workflow-id>.workflow.json \
  --dry-run
```

This is important:

- `--root .` means the run artifacts should be written under the target workspace `.local/...` tree
- the runner code stays in `<runner-checkout>`
- the task pack stays in the target workspace

### Step 14: Inspect The Dry-Run Artifacts

After the dry run, inspect:

- `.local/automation/responses_runner_v2/runs/<run-id>/run_manifest.json`
- `.local/automation/responses_runner_v2/runs/<run-id>/stages/<stage-dir>/input_manifest.json`
- `.local/automation/responses_runner_v2/runs/<run-id>/stages/<stage-dir>/input_manifest.md`
- `.local/automation/responses_runner_v2/runs/<run-id>/stages/<stage-dir>/request_payload.json`
- `.local/automation/responses_runner_v2/runs/<run-id>/stages/<stage-dir>/stage_checkpoint.json`

Verify:

- the correct files are attached
- the authority classification is correct
- the prompt and instructions are correct
- the output settings are correct

If the dry run is wrong, fix the task pack and rerun the dry run.

### Step 15: Launch The First Live Run

Only after the dry run looks correct:

```bash
cd "<target-workspace>"

python3 "<runner-checkout>/automation/run_responses_v2.py" run \
  --root . \
  --workflow-file task_packs/<task-name>/workflows/<workflow-id>.workflow.json \
  --skip-token-count \
  --wait
```

Use `--skip-token-count` for live `run` commands.

### Step 16: Inspect The Live Artifacts

After completion, inspect:

- `.local/automation/responses_runner_v2/runs/<run-id>/stages/<stage-dir>/response.final.md`
- `.local/automation/responses_runner_v2/runs/<run-id>/stages/<stage-dir>/response.final.json`
- `.local/automation/responses_runner_v2/runs/<run-id>/stages/<stage-dir>/uploads.json`

If the workflow later uses sidecar extraction, also inspect:

- `.local/automation/responses_runner_v2/runs/<run-id>/stages/<stage-dir>/output.structured.json`

### Step 17: Use Review Bundles If The Workflow Requires Review Gates

If the workflow later grows into reviewed stages:

1. run until the stage stops for review
2. inspect the stage output
3. write reviewer notes
4. create an approved review bundle
5. continue the workflow with `--review-bundle`

### Step 18: Reuse Tier 1, Repeat Tier 2

For the next task in the same target workspace:

- do not redo Tier 1 unless a new operator joins or the runner changes materially
- update the local workspace brief
- create a new task pack
- dry-run it
- inspect it
- launch it

## Readiness Checklist

The team is runner-ready when:

- at least one operator has completed Tier 1
- that operator understands the single-root model and dry-run flow

The target workspace is launch-ready when:

- the workspace has a local `RUNNER_PLAYBOOK.md`
- the task is concrete
- the canonical source set is under the target workspace root
- the task pack exists under that root
- the dry run has been inspected manually
- the live run will use `--skip-token-count`

## Recommended Usage

Use this runbook together with:

- `<runner-checkout>/docs/runbooks/project-workspace-playbook-template.md`

The template is the copyable short brief.
This document is the full explanation of how and why the two-tier model works.
