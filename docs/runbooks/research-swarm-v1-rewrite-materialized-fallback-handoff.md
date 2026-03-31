# Research Swarm V1 Rewrite Materialized Fallback Handoff

Last updated: 2026-03-31

This document is the handoff for the next team taking over the `autonomous-agentic-research-swarm` rewrite through the fallback continuation path based on the exact materialized Stage 1 through Stage 4 baseline.

It supersedes the older continuation logic that kept retrying the original six-stage Stage 5 in the main target workspace. The six-stage Stage 5 path has now failed repeatedly with the same zero-output backend `server_error` pattern and should no longer be treated as the primary execution path.

## 1. Executive Summary

The correct next move is to continue from the fallback worktree:

- target fallback worktree: `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized`
- active fallback workflow id: `research_swarm_v1_rewrite_materialized_continuation`
- active fallback run id: `run_20260331_120151_06a3c543`
- active fallback run dir:
  `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation`

Why:

- the original five-stage terminal design failed remotely
- the split six-stage Stage 5 design also failed repeatedly
- all substantive failures were the same failure shape:
  - no assistant text
  - zero output items
  - backend `server_error`
  - no evidence of a content/schema rejection
- the fallback path is already prepared and dry-run validated
- the fallback path preserves quality by carrying forward the exact approved Stage 1 through Stage 4 baseline plus the approved Stage 4 reviewed handoff

The next team should not spend more time trying to rescue the original six-stage Stage 5 run before using the fallback.

## 2. Repositories, Worktrees, And Environment

Runner repo:

- `/Users/aeziz-local/staged-workflow-runner`

Main target workspace:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm`

Fallback materialized worktree:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized`

Current worktree list:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm` on branch `main`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized` on branch `fallback/materialized-stage1-4`

Important environment note:

- the target workspaces do not carry their own working `OPENAI_API_KEY` environment by default
- live runner commands were executed by sourcing:
  - `/Users/aeziz-local/staged-workflow-runner/.env`

Important local-only note:

- the task-pack files under `docs/feedbacks/` are local-only and gitignored in the target workspace
- do not assume the pack exists in the target repo remote
- the fallback continuation pack is local state that currently exists only in the fallback worktree filesystem

## 3. What This Rewrite Task Is

The job is a high-quality rewrite transfer package for the `autonomous-agentic-research-swarm` repository, using the repository's current L2-to-L1 rent analysis project as the battle-test case.

The approved earlier stages established:

1. diagnosis of what was strong versus incomplete
2. target architecture for a real v1 research swarm
3. exact rewrite foundations for the control plane, contracts, prompts, templates, backlog, and repo-level operating documents
4. exact runtime, gate, runtime-test, and runtime-schema surfaces

What remained after those stages:

- release assembly surfaces
- release catalog and release-status surfaces
- canonical release-manifest namespace
- Quarto paper source and build/render surfaces
- targeted final tests and any downstream-only schemas required for those surfaces

## 4. Workflow Evolution And Why We Reached The Current State

This task pack evolved several times because the terminal workload repeatedly failed remotely.

### 4.1 Legacy Reviewed Three-Stage Shape

Earlier shape:

- `diagnosis`
- `target_architecture`
- monolithic terminal implementation packet

Observed problem:

- repeated remote `server_error` failure in the heavy terminal stage

### 4.2 Four-Stage Shape

The first redesign split the heavy terminal packet so that foundations and the remaining rewrite packet were separate.

Result:

- still too heavy in the last stage
- terminal failure persisted

Archived evidence in the main target run:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/_archived_stages/04_final_rewrite_packet_from_failed_four_stage`

### 4.3 Five-Stage Shape

The next redesign split the old final layer into:

- `runtime_and_gates_packet`
- `release_and_paper_packet`

This improved Stage 4 quality and clarity, but the combined release-and-paper terminal workload still failed remotely.

Archived evidence:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/_archived_stages/05_release_and_paper_packet_from_failed_five_stage`

### 4.4 Six-Stage Shape

The old Stage 5 was then split into:

- Stage 5 `release_and_catalog_packet`
- Stage 6 `paper_packet`

This six-stage design was directionally correct and was improved carefully:

- Stage 5 explicitly owned the release-manifest namespace under `reports/status/releases/`
- Stage 6 explicitly owned the full paper build/render surface under `reports/paper/build/`
- shared authority language was aligned
- stale upstream topology wording in older prompt files was cleaned

However, the actual remote execution result still failed repeatedly for Stage 5. The failures were operational/backend failures, not output-quality review failures.

Archived six-stage attempt history in the main target run:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/_archived_stages/05_release_and_catalog_packet_from_cancelled_six_stage_submit`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/_archived_stages/05_release_and_catalog_packet_from_failed_six_stage_live_attempt_1`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/_archived_stages/05_release_and_catalog_packet_from_cancelled_six_stage_live_attempt_2`

Current final six-stage failure:

- response id: `resp_061fc764c89796600069cbae93cdf881939b23fbdc15dd45e0`
- request id in saved error message: `wfr_019d43a1f2577d30be211464155ab686`
- failure shape:
  - `status = failed`
  - `error.code = server_error`
  - `output_items = 0`
  - `No assistant text was returned.`

Relevant current failure artifacts:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/run_manifest.json`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/05_release_and_catalog_packet/response.latest.json`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/05_release_and_catalog_packet/response.final.json`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/05_release_and_catalog_packet/response.final.md`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/05_release_and_catalog_packet/stage_checkpoint.json`

## 5. Current Main-Workspace Status

Main target workspace:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm`

Current six-stage continuation run dir:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage`

Current run state:

- run id: `run_20260328_215623_34b841a1`
- workflow id: `research_swarm_v1_rewrite_four_stage`
- current stage id: `release_and_catalog_packet`
- run status: `failed`
- Stage 5 status: `failed`
- Stage 5 response id: `resp_061fc764c89796600069cbae93cdf881939b23fbdc15dd45e0`
- Stage 5 response status: `failed`

Recommendation for this run:

- preserve it as provenance
- do not relaunch it again
- do not continue Stage 6 from it
- do not spend additional time trying to make the six-stage Stage 5 path work

## 6. Approved Earlier Stages And Their Status

Approved Stage 1 diagnosis artifact:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_135926_research_swarm_v1_rewrite_research_swarm_v1_rewrite/stages/01_diagnosis/response.final.md`

Approved Stage 2 target architecture artifact:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_135926_research_swarm_v1_rewrite_research_swarm_v1_rewrite/stages/02_target_architecture/response.final.md`

Approved Stage 3 rewrite foundations artifact:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/03_rewrite_foundations/response.final.md`

Approved Stage 4 runtime and gates artifact:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/04_runtime_and_gates_packet/response.final.md`

Approved Stage 4 reviewer notes:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/review_bundles/stage4_runtime_and_gates_packet.reviewer_notes.md`

Approved Stage 4 review bundle:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/review_bundles/five_stage_stage4_runtime_and_gates_packet.review_bundle.json`

These remain the quality-critical upstream authority for the fallback continuation.

## 7. Why The Fallback Strategy Was Chosen

The fallback strategy was chosen because it is the best path that preserves quality while removing repeated remote-failure risk.

The logic was:

- materialize the approved Stage 1 through Stage 4 baseline exactly into a fresh worktree
- do not reinterpret or rewrite those prior-stage outputs during materialization
- preserve the approved Stage 4 review bundle and reviewer notes as the authoritative downstream constraint source
- run only a two-stage continuation for the remaining work:
  - `release_and_catalog_packet`
  - `paper_packet`

Why this does not compromise quality:

- it keeps the approved Stage 4 reviewed handoff model-visible
- it preserves the exact emitted Stage 3 and Stage 4 repo-facing file contents
- it removes the need to re-run diagnosis, architecture, foundations, or runtime/gates again
- it narrows the workflow to only the truly remaining surfaces

## 8. Fallback Worktree Status

Fallback worktree:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized`

Branch:

- `fallback/materialized-stage1-4`

Fallback continuation pack root:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation`

Fallback continuation workflow file:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/workflows/materialized_two_stage_continuation.workflow.json`

Fallback dry-run run dir:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation`

Fallback dry-run state:

- run id: `run_20260331_120151_06a3c543`
- run status: `created`
- current stage id: `release_and_catalog_packet`
- stage order:
  - `release_and_catalog_packet`
  - `paper_packet`

Prepared Stage 1 dry-run artifacts:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation/run_manifest.json`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation/stages/01_release_and_catalog_packet/input_manifest.json`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation/stages/01_release_and_catalog_packet/input_manifest.md`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation/stages/01_release_and_catalog_packet/request_payload.json`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation/stages/01_release_and_catalog_packet/stage_checkpoint.json`

Important nuance:

- `paper_packet` appears in the dry-run stage list, but the real execution dependency is still Stage 1 review approval first
- do not treat Stage 2 as ready to launch before a real Stage 1 reviewed handoff exists

## 9. Exact Materialization Method And Verification

Audit file:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/fallback_preparation/materialized_stage1_4_audit.json`

Materialization boundary:

- Stage 1 has no file blocks and no repo-side materialization
- Stage 2 has no file blocks and no repo-side materialization
- exact repo-side materialization begins at Stage 3

Applied exactly in the fallback worktree:

- Stage 3:
  - 23 rewrites
  - 5 new files
  - 3 removals
- Stage 4:
  - 4 rewrites
  - 7 new files
  - 1 removal

Exact-match verification result:

- expected rewritten/new files: 39
- expected removed paths: 4
- missing files: none
- mismatched files: none
- removed paths still present: none
- all files match exactly: true

Removed paths:

- `.orchestrator/backlog/T030_growthepie_etl_snapshot_and_golden_sample.md`
- `.orchestrator/backlog/T050_validation_vendor_panel_checks.md`
- `.orchestrator/backlog/T060_analysis_str_timeseries_figure.md`
- `tests/test_smoke.py`

Targeted Stage 4-owned tests rerun successfully in the fallback worktree:

- command:
  - `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest tests/test_quality_gates_repo_structure.py tests/test_swarm_role_state_semantics.py tests/test_quality_gates_integration_ready.py tests/test_quality_gates_processed_manifests.py tests/test_quality_gates_judge_operator.py`

Important test-environment note:

- a first plain `pytest` attempt failed because of globally autoloaded third-party pytest plugins in the local Python environment
- that was not a repo regression
- use `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` for targeted verification in this workspace unless the environment is cleaned

Important scope note:

- the fallback continuation pack files under `docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation` are intentionally outside the approved Stage 1 through Stage 4 materialized surface
- exactness is asserted for prior-stage-owned repo surfaces and for the mirrored review artifacts, not for the newly added local-only continuation-pack files

## 10. Fallback Continuation Pack Design

Pack README:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/README.md`

Key pack files:

- shared instructions:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/shared_instructions.md`
- mission brief:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/corpus/mission_brief.md`
- materialized baseline brief:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/corpus/materialized_baseline.md`
- Stage 1 prompt:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/prompts/stage1_release_and_catalog_packet.md`
- Stage 2 prompt:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/prompts/stage2_paper_packet.md`
- Stage 1 input manifest:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/inputs/stage1_release_and_catalog_packet.input_manifest.json`
- Stage 2 input manifest:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/inputs/stage2_paper_packet.input_manifest.json`
- mirrored approved Stage 4 review bundle:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/review_bundles/approved_stage4_runtime_and_gates_packet.review_bundle.json`
- mirrored approved Stage 4 markdown:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/review_bundles/approved_stage4_runtime_and_gates_packet.response.final.md`
- mirrored approved Stage 4 reviewer notes:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/review_bundles/approved_stage4_runtime_and_gates_packet.reviewer_notes.md`

Design choices that matter:

- the pack is two-stage only
- Stage 1 is `review_required`
- Stage 2 is `terminal`
- Stage 1 uses static reviewed handoff inputs because the new workflow begins after the approved Stage 4 boundary
- Stage 2 uses normal carry-forward from the approved Stage 1 continuation review bundle
- Stage 1 and Stage 2 both use `no_tools`
- Stage 1 keeps the approved Stage 4 markdown plus reviewer notes model-visible
- the mirrored review bundle paths were rewritten locally so the fallback pack is self-contained inside the materialized worktree

## 11. What The Next Team Should Read First

Read in this order:

1. this handoff file
2. `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/README.md`
3. `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/fallback_preparation/materialized_stage1_4_audit.json`
4. `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation/run_manifest.json`
5. `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation/stages/01_release_and_catalog_packet/input_manifest.md`
6. `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation/stages/01_release_and_catalog_packet/request_payload.json`
7. `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/prompts/stage1_release_and_catalog_packet.md`
8. `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/prompts/stage2_paper_packet.md`
9. `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/review_bundles/approved_stage4_runtime_and_gates_packet.response.final.md`
10. `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/review_bundles/approved_stage4_runtime_and_gates_packet.reviewer_notes.md`

Only after that, if needed for historical forensics, look back at the failed six-stage Stage 5 artifacts in the main workspace.

## 12. Exact Launch Procedure For The Next Team

### 12.1 Preserve Provenance

Before changing anything:

- keep the current failed six-stage run artifacts in place
- if you want a cleaner main-workspace record, archive the failed Stage 5 directory before any further local mutations, but do not erase it

### 12.2 Inspect The Prepared Fallback Stage 1 Payload

In the fallback worktree, inspect:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation/stages/01_release_and_catalog_packet/input_manifest.md`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized/.local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation/stages/01_release_and_catalog_packet/request_payload.json`

Do not skip this inspection step.

### 12.3 Launch Fallback Stage 1

From the fallback worktree:

```bash
cd "/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized"

set -a
source /Users/aeziz-local/staged-workflow-runner/.env
set +a

python3 "/Users/aeziz-local/staged-workflow-runner/automation/run_responses_v2.py" run \
  --root . \
  --workflow-file docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/workflows/materialized_two_stage_continuation.workflow.json \
  --run-dir .local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation \
  --skip-token-count \
  --wait
```

### 12.4 After Stage 1 Completes

Inspect:

- Stage 1 `response.final.md`
- Stage 1 `response.final.json`
- Stage 1 `uploads.json`

Then perform the human review step.

The next team should create or save the approved Stage 1 continuation review bundle in the fallback worktree before continuing to Stage 2.

### 12.5 Continue To Stage 2

After Stage 1 approval:

```bash
cd "/Users/aeziz-local/Research/autonomous-agentic-research-swarm-fallback-stage1-4-materialized"

set -a
source /Users/aeziz-local/staged-workflow-runner/.env
set +a

python3 "/Users/aeziz-local/staged-workflow-runner/automation/run_responses_v2.py" run \
  --root . \
  --workflow-file docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite_materialized_continuation/workflows/materialized_two_stage_continuation.workflow.json \
  --run-dir .local/automation/responses_runner_v2/runs/2026-03-31_120151_research_swarm_v1_rewrite_materialized_continuation_research_swarm_v1_rewrite_materialized_continuation \
  --review-bundle <stage-1-review-bundle.json> \
  --skip-token-count \
  --wait
```

## 13. Things The Next Team Should Not Redo

Do not redo:

- Stage 1 diagnosis analysis
- Stage 2 target architecture design
- Stage 3 materialization
- Stage 4 materialization
- fallback pack creation
- fallback dry-run preparation

Those are already done.

Do not reopen:

- Stage 3-owned files
- Stage 4-owned runtime/gate/test/schema surfaces
- the approved Stage 4 reviewer clarifications

If a real upstream defect is discovered in a locked prior-stage-owned file, record it explicitly rather than silently rewriting it inside the continuation stage.

## 14. Residual Risks And Caveats

Known caveats:

- `docs/feedbacks/` is local-only and gitignored
- the fallback pack is self-contained locally, but not committed to the target repo remote
- the original six-stage run remains failed and unarchived in its final state
- a dirty fallback worktree is expected because it intentionally contains the exact materialized Stage 3 and Stage 4 baseline plus the local continuation pack
- generic `pytest` in this environment can pick up incompatible global plugins unless plugin autoload is disabled

Known upstream issues that remain intentionally out of continuation scope unless explicitly escalated:

- the inherited `disallowed_paths` mismatch in `contracts/framework.json`
- the small `task.title` drift around run-manifest schema/validator noted in Stage 4 review
- the slightly loose review-log linkage language noted in Stage 4 review

These were already judged not to block continuation.

## 15. Bottom Line

The original six-stage Stage 5 path is no longer the right place to spend effort.

The prepared fallback continuation from the exact materialized Stage 1 through Stage 4 baseline is ready.

The next team can continue immediately from the fallback worktree without additional preparation if they:

1. inspect the prepared fallback Stage 1 payload
2. launch fallback Stage 1 live
3. review and bundle Stage 1 output
4. continue into fallback Stage 2
