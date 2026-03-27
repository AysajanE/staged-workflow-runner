# Project Workspace Playbook Template

This file is a reusable onboarding and execution playbook for teams who need to use `staged-workflow-runner` against an arbitrary target project workspace.

Keep this file generic in the runner repository.
When adapting a real project, copy it into that target workspace and replace the placeholder paths and example names.

## How To Use This Template

Replace these placeholders before operational use:

- `<target-workspace>`: path to the target project workspace
- `<task-pack-root>`: path under the target workspace where the task pack will live
- `<workflow-file>`: path to the workflow manifest under the target workspace
- `<run-dir>`: path to a concrete run directory written by the runner

This playbook is written for the team working inside the target workspace.
It assumes the runner code may live in a separate repository.

In this local environment, the runner root is fixed at:

- `/Users/aeziz-local/staged-workflow-runner`

## What The Team Must Understand First

Before creating or running a task pack in a real project workspace, the team should understand the runner itself.

Read these files in order from the runner repository:

1. `/Users/aeziz-local/staged-workflow-runner/TEAM_ONBOARDING.md`
2. `/Users/aeziz-local/staged-workflow-runner/docs/runbooks/responses-runner-v2.md`
3. `/Users/aeziz-local/staged-workflow-runner/docs/runbooks/first-use-adaptation-example.md`
4. `/Users/aeziz-local/staged-workflow-runner/automation/examples/responses_runner_v2_synthetic/README.md`
5. `/Users/aeziz-local/staged-workflow-runner/automation/responses_runner_v2/workflow.py`

The team should leave this step with these architecture facts clear:

- The runner engine is generic. Task behavior lives in workflow manifests, prompts, input manifests, tool profiles, and optional schemas.
- The first release uses one exact workspace root per run.
- All statically referenced workflow assets, review bundles, and run artifacts must stay under that same workspace root.
- Stage progression is sequential and gate-driven.
- Attachment authority order is fixed:
  1. Primary Job Inputs
  2. Reviewed Handoff Inputs
  3. Attached Repository Files
  4. Reference Context
- Dry run, resume, refresh, review bundles, and optional sidecar extraction are built into the runner.
- Live `run` commands should use `--skip-token-count` until the server-side token-preflight issue is resolved.

## Step 0: Validate The Runner Before Adapting A Real Project

Do this once per team or once per runner upgrade.

Run the proof-pack unit test:

```bash
cd /Users/aeziz-local/staged-workflow-runner

python3 -m unittest automation.tests.test_responses_runner_v2_example_pack
```

Dry-run the synthetic one-pass example:

```bash
cd /Users/aeziz-local/staged-workflow-runner

python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --dry-run
```

What the team should verify:

- the command completes cleanly
- a run directory is created under `.local/automation/responses_runner_v2/runs/`
- the stage directory contains `input_manifest.json`, `input_manifest.md`, `request_payload.json`, and `stage_checkpoint.json`

Do not start adapting a real project until this proof path is understood.

## Step 1: Decide Whether The Task Is Ready For The Runner

The runner is a good fit when the task can be expressed as a bounded workflow with explicit stage outputs and explicit source authority.

The task should be ready before pack authoring begins.

Minimum readiness checklist:

- there is a concrete task objective
- there is a canonical source set under the target workspace root
- the team knows what artifact the run should produce
- the team knows whether the task should be:
  - `one_pass`
  - `two_pass`
  - multi-stage with `review_required` gates
- the team knows which inputs are authoritative versus contextual
- the team has decided whether the task needs tools or should start with `no_tools`

Start with the smallest credible workflow.

Recommended first-real-task sequence:

1. one-pass task
2. two-pass task only after the one-pass task is reliable
3. reviewed staged workflow only after the earlier patterns are working cleanly

## Step 2: Prepare The Target Workspace

The target workspace is the operator home base for task-pack editing and run inspection.

The runner script may live elsewhere, but the target workspace should contain:

- the source files for the task
- the task pack
- any review bundles created for this task
- the `.local/automation/responses_runner_v2/runs/` output tree

Preparation checklist:

- choose the exact target workspace root
- make sure all statically referenced files are under that root
- add `.local/` to the target workspace `.gitignore` if run artifacts should stay untracked
- decide where the task pack will live under the workspace root
- make sure `OPENAI_API_KEY` is available in the environment or a `.env` file under the target workspace root

Important operating rule:

- You do not need to `cd` into the runner repository to use the runner.
- You also do not strictly need to `cd` into the target workspace.
- In practice, teams should work from the target workspace because task-pack edits and run inspection happen there.

## Step 3: Choose A Task-Pack Layout

The engine does not require a specific directory name.
`task_packs/` is a convention, not a requirement.

Recommended layout:

```text
<target-workspace>/
  task_packs/
    <task-pack-name>/
      shared_instructions.md
      prompts/
        stage1.md
        stage2.md
      inputs/
        stage1.input_manifest.json
        stage2.input_manifest.json
      workflows/
        <workflow-id>.workflow.json
      schemas/
        <schema>.schema.json
      tools/
        no_tools.profile.json
      corpus/
        optional_local_fact_sheet.md
        optional_snapshot.md
```

What each file does:

- `shared_instructions.md`: stable role, goal, grounding rules, and output contract
- `prompts/*.md`: stage-specific task instructions
- `inputs/*.input_manifest.json`: static attachment lists per stage
- `workflows/*.workflow.json`: stage order, gates, model-role selection, request defaults
- `schemas/*.schema.json`: optional sidecar structured extraction schemas
- `tools/*.profile.json`: tool availability for the stage
- `corpus/*`: optional repo-local supporting evidence snapshots

## Step 4: Author The Shared Instructions

Shared instructions should be short, stable, and cross-stage.

They should usually contain:

- one or two explicit model roles
- the stage-independent goal
- attachment authority order
- grounding rules
- an output contract
- a brief verification loop

Use this shape:

```md
<role>
You are the lead <role-1> and <role-2> for the current task.
Operate with implementation precision and a strong bias toward evidence-backed output.
</role>

<goal>
Produce the concrete output package required for this task.
</goal>

<attachment_authority_order>
1. Primary Job Inputs
2. Reviewed Handoff Inputs
3. Attached Repository Files
4. Reference Context
</attachment_authority_order>

<grounding_rules>
- Use attached material first.
- Preserve higher-authority facts and locked reviewed decisions.
- Label inferences clearly when evidence is incomplete.
</grounding_rules>

<output_contract>
- Return every required section from the stage prompt.
- Cite repo-relative paths when grounding claims.
</output_contract>
```

Authoring rules:

- Keep domain-specific detail in prompts and manifests, not in the engine
- Avoid generic motivational language
- Avoid long lists of optional roles
- Do not restate every stage requirement here

## Step 5: Author The Stage Prompt

The stage prompt is where the real task contract lives.

Good stage prompts are:

- explicit about the stage objective
- decisive about the required output shape
- concrete about tables, sections, or emitted file content
- explicit about what counts as grounded fact versus inference
- compact enough that the model spends its budget on the task rather than on decoding vague instructions

Use this shape:

```md
Produce the stage output for <task-name>.

This stage must:
- decide <decision-1>
- decide <decision-2>
- not reopen <locked-scope>

Required output:

## 0. Summary
...

## 1. Decision Table
| column_a | column_b | column_c |

## 2. Implementation Contract
...

Writing rules:
- be decisive
- cite attached repo-relative paths
- label inferences clearly
```

Prompt authoring rules:

- Choose exactly one output contract when possible.
- Use exact tables when structured comparison matters.
- Do not ask for abstract brainstorming if the team needs an implementation-ready artifact.
- If the task is high-stakes, force the model to separate evidence-backed statements from inferences.

## Step 6: Build The Input Manifest

Each stage needs a static input manifest that tells the runner which files to attach and how to classify them.

Starter example:

```json
{
  "schema_version": "responses_runner_v2.input_manifest.v1",
  "manifest_id": "<workflow-id>.<stage-id>",
  "workflow_id": "<workflow-id>",
  "stage_id": "<stage-id>",
  "description": "Static inputs for the current stage.",
  "primary_job_inputs": [
    {
      "path": "docs/approved_brief.md",
      "kind": "file",
      "notes": "authoritative brief"
    }
  ],
  "reviewed_handoff_inputs": [],
  "attached_repository_files": [
    {
      "path": "docs/fact_sheet.md",
      "kind": "file",
      "notes": "repo-local evidence"
    }
  ],
  "reference_context": []
}
```

Input-manifest rules:

- Every static path must stay under the target workspace root.
- Only put true source-of-truth task inputs in `primary_job_inputs`.
- Use `attached_repository_files` for supporting repo evidence.
- Use `reference_context` only for lower-authority context.
- Keep the authority distinction meaningful.

Attachment caveat:

- The runner can wrap unsupported UTF-8 text files into markdown at upload time.
- Do not assume binary files will be auto-wrapped.
- If a task depends on a binary file type, verify how that file will be handled before the first live run.

## Step 7: Build The Workflow Manifest

Starter one-pass example:

```json
{
  "schema_version": "responses_runner_v2.workflow_manifest.v1",
  "workflow_id": "<workflow-id>",
  "workflow_name": "<workflow-name>",
  "workflow_mode": "one_pass",
  "description": "<workflow-description>",
  "shared_instructions_file": "../shared_instructions.md",
  "operator_requirements": {
    "minimum_primary_job_inputs": 0,
    "allow_reference_context": true
  },
  "defaults": {
    "model_roles": {
      "primary_generation": {
        "model": "gpt-5.4-pro",
        "reasoning_effort": "xhigh",
        "verbosity": "high",
        "prompt_cache_retention": "in_memory"
      },
      "structural_processing": {
        "model": "gpt-5.4",
        "reasoning_effort": "xhigh",
        "verbosity": "medium",
        "prompt_cache_retention": "24h"
      }
    },
    "request": {
      "background": true,
      "store": true,
      "parallel_tool_calls": true,
      "max_tool_calls": 8,
      "temperature": 1,
      "service_tier": "default",
      "token_preflight": {
        "enabled": true,
        "max_retries": 2,
        "retryable_http_status_codes": [429, 500, 502, 503, 504],
        "on_retryable_service_failure": "continue_without_token_count"
      },
      "file_uploads": {
        "purpose": "user_data",
        "delete_on_completion": false,
        "expires_after_seconds": 604800
      }
    }
  },
  "stages": [
    {
      "stage_id": "<stage-id>",
      "stage_number": 1,
      "title": "<stage-title>",
      "task_file": "../prompts/stage1.md",
      "input_manifest_file": "../inputs/stage1.input_manifest.json",
      "tool_profile_file": "../tools/no_tools.profile.json",
      "model_role": "primary_generation",
      "max_output_tokens": 8000,
      "gate": "terminal",
      "output": {
        "primary_format": "text"
      }
    }
  ]
}
```

Workflow authoring rules:

- Start with the default model posture unless the task has a clear reason to change it.
- Usually do not set `max_input_tokens`.
- Use `no_tools` unless the task truly requires tools.
- Keep early workflows simple.
- Add reviewed gates only when human approval materially improves safety or quality.

## Step 8: Add A Tool Profile

Use this when the task should have no tools:

```json
{
  "tools": []
}
```

Only introduce tool-enabled profiles when the task genuinely needs them.

## Step 9: Pre-Launch Quality Check

Before the first dry run, check all of this:

- the workflow file resolves correctly under the target workspace root
- shared instructions and prompts are task-specific and grounded
- every manifest path exists under the target workspace root
- the authority order in the manifests is deliberate
- the output contract is concrete enough to review
- the workflow does not contain stale `.local/...` run-artifact paths
- binary attachments are understood and intentionally included
- `.local/` is gitignored if local run artifacts should stay uncommitted

## Step 10: Dry Run The Pack

Teams should normally run from the target workspace.

Example:

```bash
cd <target-workspace>

python3 /Users/aeziz-local/staged-workflow-runner/automation/run_responses_v2.py run \
  --root . \
  --workflow-file <workflow-file> \
  --dry-run
```

Inspect these artifacts immediately after the dry run:

- `<run-dir>/run_manifest.json`
- `<run-dir>/stages/<stage-number>_<stage-id>/input_manifest.json`
- `<run-dir>/stages/<stage-number>_<stage-id>/input_manifest.md`
- `<run-dir>/stages/<stage-number>_<stage-id>/request_payload.json`
- `<run-dir>/stages/<stage-number>_<stage-id>/stage_checkpoint.json`

Dry-run checklist:

- the attached files are the files the team intended to send
- the role classification is correct
- the prompt and instructions are present in the request payload
- the output settings are correct
- the run directory structure matches expectations

Do not submit a live run until the dry run looks right.

## Step 11: Launch The First Live Run

Standard first-live-run command:

```bash
cd <target-workspace>

python3 /Users/aeziz-local/staged-workflow-runner/automation/run_responses_v2.py run \
  --root . \
  --workflow-file <workflow-file> \
  --skip-token-count \
  --wait
```

Operator defaults:

- use `--skip-token-count` for live `run` commands
- use `--wait` on the first real run unless there is a reason not to
- do not add optional complexity on the first live run

Inspect after completion:

- `<run-dir>/stages/<stage-number>_<stage-id>/response.final.md`
- `<run-dir>/stages/<stage-number>_<stage-id>/response.final.json`
- `<run-dir>/stages/<stage-number>_<stage-id>/uploads.json`
- `<run-dir>/stages/<stage-number>_<stage-id>/output.structured.json` if sidecar extraction is enabled

## Step 12: Handle Reviewed Stages

If the workflow has review-required gates:

1. run until the stage stops for review
2. inspect the stage output
3. prepare reviewer notes
4. create an approved review bundle
5. continue the workflow with `--review-bundle`

Bundle creation shape:

```bash
python3 /Users/aeziz-local/staged-workflow-runner/automation/create_review_bundle_v2.py \
  --root <target-workspace> \
  --output review_bundle.json \
  --workflow-id <workflow-id> \
  --source-stage-id <stage-id> \
  --source-run-id <run-id> \
  --primary-artifact-markdown <run-dir>/stages/<stage-dir>/response.final.md \
  --response-artifact-json <run-dir>/stages/<stage-dir>/response.final.json \
  --reviewer-notes notes.md
```

Continue shape:

```bash
cd <target-workspace>

python3 /Users/aeziz-local/staged-workflow-runner/automation/run_responses_v2.py run \
  --root . \
  --workflow-file <workflow-file> \
  --run-dir <run-dir> \
  --review-bundle review_bundle.json \
  --skip-token-count \
  --wait
```

## Step 13: Resume Or Refresh A Run

Resume a nonterminal stage:

```bash
cd <target-workspace>

python3 /Users/aeziz-local/staged-workflow-runner/automation/run_responses_v2.py resume \
  --root . \
  --run-dir <run-dir> \
  --stage <stage-id> \
  --wait
```

Refresh status without resubmitting:

```bash
cd <target-workspace>

python3 /Users/aeziz-local/staged-workflow-runner/automation/run_responses_v2.py refresh \
  --root . \
  --run-dir <run-dir> \
  --stage <stage-id>
```

Use `resume` when the runner should continue to terminal completion and finalize artifacts.
Use `refresh` when the team only needs the latest remote status written locally.

## Standard Operating Rules

- Keep the runner engine generic.
- Keep target-specific behavior in prompts, manifests, schemas, and review policy.
- Keep every statically referenced file under the chosen target workspace root.
- Do not bypass the authority hierarchy casually.
- Dry-run every new pack before the first live submission.
- Start with the smallest credible task.
- Prefer task-pack edits over engine edits unless the target exposes a real framework gap.
- Use exact repo-relative citations in prompt outputs when grounded claims matter.

## Final Launch Checklist

The team is ready to launch when all of the following are true:

- the runner proof pack was understood and dry-run successfully
- the target task is concrete and the source set is under the target workspace root
- the task pack exists and all referenced files resolve
- the dry run was inspected manually
- the output contract is specific enough to review
- live `run` will use `--skip-token-count`
- the team knows where the run artifacts will be inspected

At that point, the first real run should be operationally straightforward.
