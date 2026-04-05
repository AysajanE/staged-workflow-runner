# Responses Runner V2 Supervisory-Lane Self-Improvement Pack

This task pack is for running the current staged workflow runner against the task of improving itself.

The target outcome is a drop-in-ready package for this repository that adds an integrated automation/supervisory lane around the existing `responses_runner_v2` engine.

## What This Pack Is For

Use this pack when the team wants the current runner to produce the design and final drop-in packet for:

- integrated task-intake clarification flow
- mandatory scaffold review before costly stage execution
- live stage monitoring and health checks
- stage-output substantive review and reviewer-note creation
- approved review-bundle preparation between stages
- failure handling that distinguishes true failure from practically retrievable failed runs
- final approved implementation bundle generation

This pack is intentionally written in the stronger scaffold style used by the latest successful reviewed-three-stage packs:

- explicit contract-block shared instructions
- exact section and table contracts in every stage prompt
- stage-curated input manifests that use file entries or targeted directory entries with exclusions based on signal density
- tighter operator-input rules so the pack stays self-contained

## Why This Pack Uses Three Stages

This task is broad and high-stakes enough that compressing it to two stages would force architecture, packet drafting, and final hardening into one overly dense pass.

The three stages are:

1. `architecture_blueprint`
   Produces the architecture, operating contract, recovery model, and minimum-change repo integration plan.
2. `draft_drop_in_packet`
   Produces the draft drop-in file set with exact repo paths and full file contents.
3. `final_drop_in_packet`
   Hardens the package after review and emits the final drop-in-ready packet, including a root `AGENTS.md`.

Each stage is deliberately non-trivial.

## Manual Review For This Round

This pack does not bundle review checklists or reviewer-note templates.

That is intentional. The future supervisory lane is supposed to design those artifacts as part of the task, so this scaffold should not preauthor them.

For this run, handle scaffold review and stage-output review manually using your normal operator process.
Reviewer notes should still explicitly state:

- what is approved
- what is not approved
- what the next stage must address first

## Pack Layout

- `shared_instructions.md`
- `corpus/`
- `prompts/`
- `inputs/`
- `schemas/`
- `tools/stage1_web_search.profile.json`
- `tools/stage2_web_search.profile.json`
- `tools/stage3_web_search.profile.json`
- `workflows/three_stage.workflow.json`

## Recommended Commands

Dry run stage 1:

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervisory_lane/workflows/three_stage.workflow.json \
  --dry-run
```

Run stage 1 live and wait:

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervisory_lane/workflows/three_stage.workflow.json \
  --skip-token-count \
  --wait
```

Create the approved review bundle after stage 1:

```bash
python3 automation/create_review_bundle_v2.py \
  --root . \
  --output <run_dir>/stage1.review_bundle.json \
  --workflow-id responses_runner_v2_supervisory_lane_self_improvement \
  --source-stage-id architecture_blueprint \
  --source-run-id <run_id> \
  --primary-artifact-markdown <run_dir>/stages/01_architecture_blueprint/response.final.md \
  --response-artifact-json <run_dir>/stages/01_architecture_blueprint/response.final.json \
  --reviewer-notes <run_dir>/stage1.review.md
```

Continue with stage 2:

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervisory_lane/workflows/three_stage.workflow.json \
  --run-dir <run_dir> \
  --review-bundle <run_dir>/stage1.review_bundle.json \
  --skip-token-count \
  --wait
```

Repeat the same pattern for stage 2 review and then continue to stage 3.

## Operational Notes

- Use `--skip-token-count` for live runs unless the service-side token-preflight issue is known to be resolved.
- Web search is enabled in all three stages. Stage 1 has the highest tool-call budget because it does the broadest external and currentness-sensitive research; stages 2 and 3 taper that budget as the task narrows into drafting and hardening.
- Keep reviewer notes and review bundles inside the same workspace root as the run.
- Do not rewrite the stage architecture in later stages unless review findings require it.
- The final stage output is expected to be directly droppable into this repository without reinterpretation.
