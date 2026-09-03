# Project Workspace Launch Brief Template

This file is the copyable workspace-local brief for using `staged-workflow-runner` against a specific target project.

Use it inside a target workspace.
Keep deep runner architecture learning centralized in the runner repository.

## What This File Is For

This file should answer only the questions an operator needs inside the target workspace:

- what task is being run
- where the workspace root is
- where the task pack lives
- which source files are canonical
- what commands to run
- what artifacts to inspect
- what caveats matter for this workspace

This file should not try to re-teach the full runner architecture every time.

## One-Time Runner Onboarding

New operators should read these once from the runner repository:

1. `<runner-checkout>/DEVELOPING.md`
2. `<runner-checkout>/docs/runbooks/responses-runner-v2.md`
3. `<runner-checkout>/docs/runbooks/first-use-adaptation-example.md`
4. `<runner-checkout>/automation/examples/responses_runner_v2_synthetic/README.md`

If the operator already knows the runner and has already done the synthetic proof path, this brief should be enough.

## Fixed Runner Path

Runner root for this local environment:

- `<runner-checkout>`

## Fill In For This Workspace

Replace the values below when copying this file into a real project workspace:

- Target workspace root: `<target-workspace>`
- Task objective: `<task-objective>`
- Task-pack root: `<task-pack-root>`
- Workflow file: `<workflow-file>`
- Expected primary artifact: `<expected-primary-artifact>`
- Canonical source files:
  - `<source-file-1>`
  - `<source-file-2>`
  - `<source-file-3>`
- Current caveats:
  - `<caveat-1>`

## Standard Team Workflow

1. Confirm the task is concrete and the canonical source set is under the target workspace root.
2. Prepare or update the task pack under that same workspace root.
3. Dry-run from the target workspace.
4. Inspect the generated request and attachment manifests.
5. Launch the live run from the target workspace with token preflight enabled and `--wait`.
6. If a stage stops at a `human` gate (or a `reviewed` gate is blocked), read `artifact.md`, write a handoff note, and continue with `--handoff-note`.

## Minimum Task-Pack Shape

```text
<target-workspace>/
  task_packs/
    <task-pack-name>/
      shared_instructions.md
      prompts/
        stage1.md
      inputs/
        stage1.input_manifest.json
      workflows/
        <workflow-id>.workflow.json
      tools/
        no_tools.profile.json
      schemas/
        optional.schema.json
```

## Pre-Launch Checklist

- all statically referenced files are under the target workspace root
- the workflow file resolves correctly under that root
- prompts and shared instructions are task-specific and grounded
- input-manifest authority order is deliberate
- the workflow does not contain stale `.local/...` artifact paths
- binary attachments are intentional and understood
- `.local/` is gitignored if local artifacts should stay uncommitted

## Commands

Dry run:

```bash
cd "<target-workspace>"

python "<runner-checkout>/automation/run_responses_v2.py" run \
  --root . \
  --workflow-file <workflow-file> \
  --dry-run
```

First live run:

```bash
cd "<target-workspace>"

python "<runner-checkout>/automation/run_responses_v2.py" run \
  --root . \
  --workflow-file <workflow-file> \
  --wait
```

Resume a nonterminal stage:

```bash
cd "<target-workspace>"

python "<runner-checkout>/automation/run_responses_v2.py" resume \
  --root . \
  --run-dir <run-dir> \
  --stage <stage-id> \
  --wait
```

Refresh remote status without resubmitting:

```bash
cd "<target-workspace>"

python "<runner-checkout>/automation/run_responses_v2.py" refresh \
  --root . \
  --run-dir <run-dir> \
  --stage <stage-id>
```

`reviewed` gates run in-process (one reviewer CLI, `codex` by default; override with `--reviewer codex|claude|none`). A `human` gate, or a reviewed stage blocked after its one revision, stops the run in `waiting_for_review`. Read `artifact.md`, write a markdown note, and continue:

```bash
cd "<target-workspace>"

python "<runner-checkout>/automation/run_responses_v2.py" run \
  --root . \
  --workflow-file <workflow-file> \
  --run-dir <run-dir> \
  --handoff-note <note.md>
```

A workflow that still declares `review_required` is loaded as a `human` gate and follows the same `--handoff-note` path.

## What To Inspect

After a dry run:

- `<run-dir>/run_manifest.json`
- `<run-dir>/dry_runs/stages/<stage-dir>/input_manifest.json`
- `<run-dir>/dry_runs/stages/<stage-dir>/input_manifest.md`
- `<run-dir>/dry_runs/stages/<stage-dir>/request_payload.json`
- `<run-dir>/dry_runs/stages/<stage-dir>/upload_inputs/`

After a live run:

- `<run-dir>/stages/<stage-dir>/<attempt_NNN>/artifact.md`
- `<run-dir>/stages/<stage-dir>/<attempt_NNN>/response.final.json`
- `<run-dir>/stages/<stage-dir>/<attempt_NNN>/uploads.json`
- `<run-dir>/stages/<stage-dir>/<attempt_NNN>/output.structured.json` when the stage declares `output.primary_format: "json_schema"`
- `<run-dir>/stages/<stage-dir>/<attempt_NNN>/validator_report.json` (advisory) when the stage configures `post_output_validators`
- `<run-dir>/stages/<stage-dir>/<attempt_NNN>/review/verdict.json` and `review/reviewer_notes.md` for `reviewed` stages

## Operating Rules

- one exact workspace root per run
- keep task-specific behavior in prompts, manifests, schemas, and review policy
- keep token preflight enabled for live critical workflows
- dry-run every new task pack before the first live submission
- do not assume binary files will be auto-wrapped
- prefer task-pack edits over runner-engine edits unless the target exposes a real framework gap

## Future Tasks In The Same Workspace

For a new task in the same project:

1. create a new task-pack directory under `task_packs/`
2. author the shared instructions and prompt or prompts
3. build the input manifest or manifests
4. build the workflow manifest
5. start with `no_tools` unless tools are truly required
6. dry-run first
7. inspect the generated request before launching live
