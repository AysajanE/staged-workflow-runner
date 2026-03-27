# First-Use Adaptation Example

This document is a **non-binding** first-use example for pointing `staged-workflow-runner` at a representative external project that needs a high-stakes architecture review and improvement package.

Use it to copy shape, not to lock names, paths, prompts, or domain policy.

Before adapting a real target, validate the runner itself with the bundled synthetic proof pack.

## What Stays Core

These surfaces should transfer unchanged from the standalone runner repository:

- `automation/responses_runner_v2/`
- `automation/run_responses_v2.py`
- `automation/create_review_bundle_v2.py`
- `automation/run_responses_v2_eval.py`
- `automation/evals/responses_runner_v2.eval.json`
- `automation/examples/responses_runner_v2_synthetic/` for runner validation only

## What Changes Per Target

These surfaces are task-specific and should normally be created inside the external project workspace:

- shared instructions
- stage prompts
- stage input manifests
- workflow manifest
- output schemas
- tool profiles
- target-project corpus snapshots or fact sheets
- reviewer notes conventions and review policy

## Representative Target Layout

The `task_packs/` segment below is illustrative, not required by the engine. Any location under the chosen workspace root is acceptable.

```text
<target-workspace>/
  docs/
    architecture_brief.md
  task_packs/
    architecture_review/
      shared_instructions.md
      corpus/
        repo_inventory.md
        system_constraints.md
      prompts/
        stage1_contract.md
        stage2_package.md
        stage3_transfer.md
      inputs/
        stage1.input_manifest.json
        stage2.input_manifest.json
        stage3.input_manifest.json
      workflows/
        architecture_review.workflow.json
      schemas/
        final_transfer.schema.json
      tools/
        no_tools.profile.json
```

## Representative Workflow Shape

A high-stakes external-project adaptation can usually stay within the existing staged runner model:

1. **Stage 1 — contract or assessment pass**  
   Establish the governing contract from the approved brief and attached repo evidence.

2. **Stage 2 — package or redesign pass**  
   Expand the approved contract into a concrete package, redesign, or implementation transfer set.

3. **Stage 3 — final transfer pass**  
   Assemble the final packet, acceptance gate, and operator checklist.

That shape mirrors the reviewed staged pattern already exercised by the bundled synthetic workflow, while keeping task-specific variation in prompts, manifests, and review policy rather than engine rewrites.

## First Run Sequence

Dry-run the external task pack from the runner checkout:

```bash
python3 /path/to/staged-workflow-runner/automation/run_responses_v2.py run \
  --root /path/to/target-workspace \
  --workflow-file task_packs/architecture_review/workflows/architecture_review.workflow.json \
  --primary-job-input docs/architecture_brief.md \
  --dry-run
```

Then run the first live stage:

```bash
python3 /path/to/staged-workflow-runner/automation/run_responses_v2.py run \
  --root /path/to/target-workspace \
  --workflow-file task_packs/architecture_review/workflows/architecture_review.workflow.json \
  --primary-job-input docs/architecture_brief.md \
  --wait
```

If the workflow uses `review_required` gates, create an approved review bundle with `automation/create_review_bundle_v2.py`, then continue later stages with `--review-bundle`.

## Portability Notes

- In the first release, the task pack, review bundles, and run outputs must all live under the same target workspace root.
- The runner checkout may be separate from the target workspace, but the engine still enforces one exact root per run.
- Attached evidence remains the default posture.
- Web-enabled evidence gathering stays additive through task-pack tool profiles rather than becoming engine-default behavior.
- Start with the default model posture unless the task pack has a specific reason to override it.

## Why This Example Stays Non-Binding

This file is only a portability pressure test.

It does **not** define:

- a required directory name
- a required workflow id
- a required prompt set
- a required evaluation policy
- a required domain or repository type

Copy the structure that fits the target project. Keep the engine unchanged unless the target exposes a real framework gap.
