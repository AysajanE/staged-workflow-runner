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

1. `<runner-checkout>/TEAM_ONBOARDING.md`
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
5. Launch the live run from the target workspace with `--skip-token-count --wait`.
6. If the workflow has review gates, create an approved review bundle and continue.

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

python3 "<runner-checkout>/automation/run_responses_v2.py" run \
  --root . \
  --workflow-file <workflow-file> \
  --dry-run
```

First live run:

```bash
cd "<target-workspace>"

python3 "<runner-checkout>/automation/run_responses_v2.py" run \
  --root . \
  --workflow-file <workflow-file> \
  --skip-token-count \
  --wait
```

Resume a nonterminal stage:

```bash
cd "<target-workspace>"

python3 "<runner-checkout>/automation/run_responses_v2.py" resume \
  --root . \
  --run-dir <run-dir> \
  --stage <stage-id> \
  --wait
```

Refresh remote status without resubmitting:

```bash
cd "<target-workspace>"

python3 "<runner-checkout>/automation/run_responses_v2.py" refresh \
  --root . \
  --run-dir <run-dir> \
  --stage <stage-id>
```

If the workflow uses reviewed gates, create a review bundle:

```bash
python3 "<runner-checkout>/automation/create_review_bundle_v2.py" \
  --root "<target-workspace>" \
  --output review_bundle.json \
  --workflow-id <workflow-id> \
  --source-stage-id <stage-id> \
  --source-run-id <run-id> \
  --primary-artifact-markdown <run-dir>/stages/<stage-dir>/response.final.md \
  --response-artifact-json <run-dir>/stages/<stage-dir>/response.final.json \
  --reviewer-notes notes.md
```

Continue after review:

```bash
cd "<target-workspace>"

python3 "<runner-checkout>/automation/run_responses_v2.py" run \
  --root . \
  --workflow-file <workflow-file> \
  --run-dir <run-dir> \
  --review-bundle review_bundle.json \
  --skip-token-count \
  --wait
```

## What To Inspect

After a dry run:

- `<run-dir>/run_manifest.json`
- `<run-dir>/stages/<stage-dir>/input_manifest.json`
- `<run-dir>/stages/<stage-dir>/input_manifest.md`
- `<run-dir>/stages/<stage-dir>/request_payload.json`
- `<run-dir>/stages/<stage-dir>/stage_checkpoint.json`

After a live run:

- `<run-dir>/stages/<stage-dir>/response.final.md`
- `<run-dir>/stages/<stage-dir>/response.final.json`
- `<run-dir>/stages/<stage-dir>/uploads.json`
- `<run-dir>/stages/<stage-dir>/output.structured.json` if sidecar extraction is enabled

## Operating Rules

- one exact workspace root per run
- keep task-specific behavior in prompts, manifests, schemas, and review policy
- use `--skip-token-count` for live `run` commands
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
