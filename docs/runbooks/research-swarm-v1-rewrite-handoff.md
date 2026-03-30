# Research Swarm V1 Rewrite Handoff

Last updated: 2026-03-30

This document is the continuation handoff for the `autonomous-agentic-research-swarm` rewrite task pack. It is intended for the next team taking over from the current team inside `/Users/aeziz-local/staged-workflow-runner`.

## 1. What This Task Is

The target workspace is:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm`

The local task-pack root in that workspace is:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite`

The active run directory is:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage`

The mission of the task pack is to produce a high-quality rewrite transfer package for the research swarm repo. The rewrite is staged:

1. diagnose current repo gaps
2. define target architecture
3. lock foundations
4. lock runtime and gates
5. lock release and paper layer

The important reality is that this late-stage work has repeatedly hit remote `server_error` failures when the final output workload is too coupled, even when the raw input payload is not especially large.

## 2. Current Status

The active run id is:

- `run_20260328_215623_34b841a1`

The active workflow id is still:

- `research_swarm_v1_rewrite_four_stage`

That name is historical only. The currently active pack topology is five-stage, not four-stage.

Current stage statuses in the active run:

- Stage 1 `diagnosis`: completed earlier, reviewed, carried forward
- Stage 2 `target_architecture`: completed earlier, reviewed, carried forward
- Stage 3 `rewrite_foundations`: completed successfully in the active run, reviewed and approved
- Stage 4 `runtime_and_gates_packet`: completed successfully in the active run, reviewed and approved
- Stage 5 `release_and_paper_packet`: failed remotely with `server_error`

The active run manifest currently shows:

- `status = failed`
- `current_stage_id = release_and_paper_packet`

The authoritative file is:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/run_manifest.json`

## 3. Important Repo And Runner Context

The runner repo is:

- `/Users/aeziz-local/staged-workflow-runner`

The target workspace does not have its own `.env` with `OPENAI_API_KEY`. Live launches in the target workspace were done by sourcing:

- `/Users/aeziz-local/staged-workflow-runner/.env`

The target workspace local task-pack files live under `docs/feedbacks/`, which is gitignored/local-only in that target repo. The next team should not assume these pack changes are committed to the target workspace remote.

There is one runner-repo code change that matters operationally:

- commit `3b80c3c` in `/Users/aeziz-local/staged-workflow-runner`
- message: `Allow review handoff to omit raw response JSON`

Why it matters:

- downstream reviewed handoff can now omit `response.final.json`
- this was used to reduce model-visible reviewed handoff size for late stages
- the workflow flag is `review_bundle_include_response_artifact_json: false`

## 4. Workflow Evolution So Far

The rewrite pack did not start in its current shape.

Original shape:

- legacy reviewed three-stage workflow
- terminal monolithic final packet
- repeated remote failure

First redesign:

- split old Stage 3 into:
  - Stage 3 `rewrite_foundations`
  - Stage 4 `final_rewrite_packet`
- switched to `custom_ordered`
- still failed at the late heavy packet

Second redesign:

- split old Stage 4 into:
  - Stage 4 `runtime_and_gates_packet`
  - Stage 5 `release_and_paper_packet`
- active workflow file became:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/workflows/five_stage_rewrite.workflow.json`

Result:

- Stage 3 succeeded
- Stage 4 succeeded
- Stage 5 failed remotely after about 3 hours with zero output

## 5. Files The Next Team Should Read First

In the target workspace, read these first:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/README.md`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/RUNNER_PLAYBOOK.md`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/workflows/five_stage_rewrite.workflow.json`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/prompts/stage3_rewrite_foundations.md`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/prompts/stage4_runtime_and_gates_packet.md`
- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/prompts/stage5_release_and_paper_packet.md`

Then read the approved handoff artifacts:

- Stage 3 output:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/03_rewrite_foundations/response.final.md`
- Stage 3 reviewer notes:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/review_bundles/stage3_rewrite_foundations.reviewer_notes.md`
- Stage 3 review bundle:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/review_bundles/five_stage_stage3_rewrite_foundations.review_bundle.json`
- Stage 4 output:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/04_runtime_and_gates_packet/response.final.md`
- Stage 4 reviewer notes:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/review_bundles/stage4_runtime_and_gates_packet.reviewer_notes.md`
- Stage 4 review bundle:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/review_bundles/five_stage_stage4_runtime_and_gates_packet.review_bundle.json`

Then inspect the failed Stage 5 prepared and terminal artifacts:

- prepared manifest:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/05_release_and_paper_packet/input_manifest.md`
- request payload:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/05_release_and_paper_packet/request_payload.json`
- terminal failure artifact:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/05_release_and_paper_packet/response.final.md`
- remote snapshot:
  - `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/05_release_and_paper_packet/response.latest.json`

## 6. Quality Assessment Summary By Stage

Stage 1 `diagnosis`

- Overall judgment: strong
- Main value: correctly diagnosed a strong control plane but weak empirical closure
- Useful enough to preserve as the basis for later stages

Stage 2 `target_architecture`

- Overall judgment: good, but aggressive
- Main value: coherent architecture and rewrite direction
- Main caution: scope inflation risk if treated as an implementation mandate instead of a prioritization document

Stage 3 `rewrite_foundations`

- Overall judgment: high quality after corrective review
- Main corrective clarifications that were locked:
  - contract-edit authority needed precise interpretation
  - `disallowed_paths` must remain required
  - release manifest filename pattern had to be fixed to `reports/status/releases/release_<YYYY-MM-DD>.json`
- Final state: approved and carried forward

Stage 4 `runtime_and_gates_packet`

- Overall judgment: high quality and good enough to carry forward
- Main strengths:
  - explicit locked runtime model
  - clean Stage 4/Stage 5 boundary
  - strong runtime, gate, test, and schema packet
- Narrow review findings that were carried forward into notes:
  - inherited `disallowed_paths` contract mismatch remains upstream and out of Stage 5 scope
  - review-log linkage is still slightly looser than the packet rhetoric implies
  - run-manifest schema and validator have a small drift on `task.title`
- Final state: approved and bundled for Stage 5

Stage 5 `release_and_paper_packet`

- Overall judgment: failed remotely, no substantive output to review
- Important clarification:
  - Stage 5 was not asking the model to execute the research or run the analysis pipeline
  - Stage 5 was asking for the actual implementation transfer package for the release assembly layer and the actual Quarto paper source files

## 7. Important Review Bundles And Notes

Stage 3 approved bundle:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/review_bundles/five_stage_stage3_rewrite_foundations.review_bundle.json`

Stage 3 reviewer notes:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/review_bundles/stage3_rewrite_foundations.reviewer_notes.md`

Stage 4 approved bundle:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/review_bundles/five_stage_stage4_runtime_and_gates_packet.review_bundle.json`

Stage 4 reviewer notes:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/review_bundles/stage4_runtime_and_gates_packet.reviewer_notes.md`

The Stage 4 reviewer notes already include the most important fence for continuation:

- Stage 5 must not reopen Stage 4-owned runtime, gate, test, or schema surfaces
- the unresolved `disallowed_paths` mismatch in `contracts/framework.json` is out of scope for Stage 5
- Stage 5 must integrate with, not redefine, the Stage 4 runtime artifacts and semantics
- `reports/status/` is now mixed ownership:
  - `swarm_runs/` and `reviews/` are locked by Stage 4
  - release docs and release manifests remain downstream

## 8. Late-Stage Failure Pattern So Far

Repeated pattern across the late stages:

- request uploads complete successfully
- remote response enters `in_progress`
- long background runtime
- terminal `server_error`
- `usage = null`
- `output = []`

This pattern has appeared on:

- earlier monolithic old Stage 3 / old Stage 4 designs
- current Stage 5 `release_and_paper_packet`

It is not consistent with:

- local packaging failure
- unsupported file failure
- broken reviewed handoff wiring

It is much more consistent with:

- workload-shape brittleness on a large exact-output synthesis stage

## 9. Current Stage 5 Failure Facts

Current failed Stage 5 response:

- response id: `resp_08041ae6d6afbc600069ca6237bd1c8196ac5df4fde652f311`
- remote status: `failed`
- error code: `server_error`
- request id inside error message: `wfr_019d3e8faab77ed889a278d8628a7c3c`
- runtime before failure: about `3h 0m 8s`
- output returned: none
- usage block: none

The authoritative file is:

- `/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/05_release_and_paper_packet/response.latest.json`

## 10. Current Stage 5 Payload Shape

Prepared Stage 5 payload breakdown from the latest failed attempt:

- primary inputs: `2,750` bytes
- reviewed handoff inputs: `218,564` bytes
- attached repo files: `21,357` bytes
- total uploaded bytes: about `250,581`
- total uploaded files: `29`

The dominant input is:

- approved Stage 4 markdown at `212,516` bytes

The attached repo context is not the main size driver.

This matters because it argues against a simplistic "too many attached repo files" diagnosis.

## 11. Why The Current Stage 5 Likely Failed

The strongest current diagnosis is:

- not raw input size
- not review-bundle authority
- not runner submission
- not file uploads

The likely bottleneck is the Stage 5 workload shape.

Current Stage 5 asks for one exact final packet that combines:

- release assembly code
- release catalog logic
- release/status docs
- release integrity tests
- Quarto paper source files
- bibliography
- paper-facing checks

This is a coupled synthesis problem across code, manifest, doc, and manuscript surfaces. That is the same failure pattern that broke earlier monolithic late stages.

## 12. Recommended Next Move

Do not retry current Stage 5 unchanged.

Recommended redesign:

Split current Stage 5 into a new Stage 5 and new Stage 6.

Recommended new Stage 5:

- `release_and_catalog_packet`
- owns:
  - `scripts/release_assembly.py`
  - `reports/AGENTS.md`
  - `reports/catalog.yaml`
  - `reports/status/README.md`
  - release manifest semantics
  - release/catalog integrity tests
  - release-specific schemas only if needed

Recommended new Stage 6:

- `paper_packet`
- owns:
  - `reports/paper/README.md`
  - `reports/paper/_quarto.yml`
  - `reports/paper/index.qmd`
  - `reports/paper/references.bib`
  - paper/render checks only if truly needed

Why this is the recommended move:

- it preserves the full approved Stage 4 reviewed handoff
- it does not force lossy summary compression
- it separates code/catalog/release synthesis from manuscript synthesis
- it attacks the likely cause, which is cross-surface output coupling
- it keeps the quality bar intact

## 13. Important Non-Recommendations

Do not do these first:

- do not retry current Stage 5 unchanged
- do not replace the full approved Stage 4 markdown with a short curated summary
- do not trim the small attached repo files first
- do not reopen Stage 3- or Stage 4-owned surfaces inside the next stage

Why not:

- unchanged retry has low expected value
- curated summary introduces quality risk
- attached repo files are not the main load driver
- reopening upstream layers would violate approved boundaries

## 14. Recommended Six-Stage Topology

Keep the existing approved earlier stages unchanged:

1. `diagnosis`
2. `target_architecture`
3. `rewrite_foundations`
4. `runtime_and_gates_packet`

Replace current failed terminal stage with:

5. `release_and_catalog_packet`
6. `paper_packet`

Recommended carry-forward:

- new Stage 5 should consume the approved Stage 4 review bundle
- new Stage 6 should consume the approved new Stage 5 review bundle
- keep `review_bundle_include_response_artifact_json: false`
- keep full approved markdown plus reviewer notes model-visible

## 15. Operational Nuances The Next Team Must Know

Always launch live runs with:

- `--skip-token-count`

When continuing a reviewed workflow stage, pass the approved review bundle explicitly on the command line, even if the prior stage summary already records the review bundle path.

Example pattern:

```bash
cd "/Users/aeziz-local/Research/autonomous-agentic-research-swarm"
set -a; source "/Users/aeziz-local/staged-workflow-runner/.env"; set +a

python3 "/Users/aeziz-local/staged-workflow-runner/automation/run_responses_v2.py" run \
  --root . \
  --workflow-file docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/workflows/<next-workflow>.workflow.json \
  --run-dir .local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage \
  --review-bundle docs/feedbacks/runner_task_packs/research_swarm_v1_rewrite/review_bundles/<next-approved-bundle>.review_bundle.json \
  --skip-token-count \
  --dry-run
```

The authoritative live status file is always:

- `stages/<stage_id>/response.latest.json`

Do not trust stale embedded response-status fields in `run_manifest.json` or `stage_checkpoint.json` over `response.latest.json`.

## 16. Exact Next Steps For The New Team

1. Read this handoff and the files listed in Section 5.
2. Read the approved Stage 4 reviewer notes and review bundle carefully.
3. Create a six-stage local workflow from the current five-stage pack.
4. Split current `stage5_release_and_paper_packet.md` into:
   - new Stage 5 release/catalog prompt
   - new Stage 6 paper prompt
5. Split the current Stage 5 input manifest into:
   - new Stage 5 release/catalog manifest
   - new Stage 6 paper manifest
6. Keep the reviewed handoff contract intact.
7. Dry-run the new Stage 5 continuation against the current active run and the approved Stage 4 review bundle.
8. Inspect the new prepared Stage 5 payload before any live run.
9. Launch the new Stage 5 only after the split looks clean.
10. If the new Stage 5 succeeds, review it carefully before preparing new Stage 6.

## 17. Useful Commands

Inspect current run status:

```bash
python3 - <<'PY'
import json
from pathlib import Path
run = Path("/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/run_manifest.json")
data = json.loads(run.read_text())
print(data["status"], data["current_stage_id"])
for s in data["stages"]:
    print(s["stage_number"], s["stage_id"], s["status"], s.get("review_bundle_path"))
PY
```

Inspect failed Stage 5 remote response:

```bash
python3 - <<'PY'
import json
from pathlib import Path
p = Path("/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/05_release_and_paper_packet/response.latest.json")
data = json.loads(p.read_text())
print(data["id"])
print(data["status"])
print(data["error"])
print(data["usage"])
print(len(data.get("output") or []))
PY
```

Inspect prepared Stage 5 manifest:

```bash
sed -n '1,260p' "/Users/aeziz-local/Research/autonomous-agentic-research-swarm/.local/automation/responses_runner_v2/runs/2026-03-28_215623_research_swarm_v1_rewrite_four_stage_research_swarm_v1_rewrite_four_stage/stages/05_release_and_paper_packet/input_manifest.md"
```

## 18. Final Judgment

The project is not blocked by runner architecture uncertainty anymore. The runner and reviewed handoff mechanics are good enough to continue. The blocker is now the shape of the final workload.

The approved Stage 3 and Stage 4 outputs are strong enough to preserve as locked inputs.

The most likely successful path forward is:

- keep approved Stage 3 and Stage 4 unchanged
- do not compress the reviewed handoff into a lossy summary
- split current Stage 5 into a release/catalog stage and a paper stage
- continue from the current active run and approved Stage 4 review bundle

