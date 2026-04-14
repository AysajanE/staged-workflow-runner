# Responses Runner V2

`responses_runner_v2` is the core engine package used by the `staged-workflow-runner` repository.

The first release intentionally keeps the current `automation/...` layout, `responses_runner_v2` package path, CLI filenames, and schema identifiers so the tested engine can transfer with minimal code churn. The public repository identity changes; the runtime architecture does not.

## Main Entry Points

- Generic CLI: `automation/run_responses_v2.py`
- Review-bundle CLI: `automation/create_review_bundle_v2.py`
- Eval CLI: `automation/run_responses_v2_eval.py`
- Operator runbook: `docs/runbooks/responses-runner-v2.md`

## First-Release Operating Model

- The runner ships as a standalone tool repository.
- Each invocation operates against **one exact workspace root**.
- Resolution order is:
  1. explicit CLI `--root`
  2. `RESPONSES_RUNNER_V2_ROOT`
  3. current working directory as-is
- There is no repo-marker search and no fallback to the runner module location.
- Workflow manifests, static attachments, review bundles, carry-forward artifacts, and run outputs must all remain under that one workspace root.
- There is no dual-root support in the first release.
- Task packs can live anywhere under the workspace root because asset references are already resolved relative to the workflow manifest.

## Core Design

The package separates four concerns:

1. **Engine code**  
   Loads task packs, validates inputs, builds Responses API payloads, polls responses, persists artifacts, and enforces reviewed handoff rules.

2. **Task-pack configuration**  
   Workflow manifests, stage input manifests, prompt files, tool profiles, and optional structured-output schemas define a workflow without requiring a new Python wrapper.

3. **Runtime operator input**  
   The operator supplies primary job inputs, approved review bundles, and optional runtime overrides through the generic CLI.

4. **Durable local artifacts**  
   Each run writes manifests, request payloads, checkpoints, raw responses, rendered markdown, and optional sidecar JSON under a per-run directory.

## Authority Model

Every stage uses the same fixed attachment authority order:

1. Primary Job Inputs
2. Reviewed Handoff Inputs
3. Attached Repository Files
4. Reference Context

The stage-local `input_manifest.md` is the human-readable source of truth for what was attached.

## Core Modules

- `contracts.py`  
  Shared constants, schema versions, authority roles, runtime options, model caps, and common helpers.

- `pack_loader.py`  
  Loads and validates workflow manifests, input manifests, tool profiles, and schema references.

- `workflow.py`  
  Main orchestration engine. Selects the next eligible stage, resolves operator inputs and review bundles, builds request payloads, handles token preflight, submits requests, waits, resumes, refreshes, and finalizes stage artifacts.

- `attachments.py`  
  Resolves files and directories into concrete attachment lists, wraps unsupported text files as markdown when needed, enforces byte limits, uploads files, and renders `input_manifest.md`.

- `openai_client.py`  
  Thin OpenAI API client for `/responses`, `/responses/input_tokens`, and `/files`.

- `artifacts.py`  
  Defines the on-disk run layout and writes run manifests, stage checkpoints, request payloads, response artifacts, structured output, and sidecar output.

- `review_bundle.py`  
  Defines the reviewed-handoff contract used between stages that require human approval.

- `sidecar.py`  
  Built-in structured extraction pass that can turn a completed markdown artifact into strict JSON using a secondary model.

## Task-Pack Contract

A specific workflow is defined by a task pack. A pack normally includes:

- one workflow manifest
- one static input manifest per stage
- one shared instructions file
- one task prompt per stage
- zero or one tool profile per stage
- optional output schema files
- optional task-specific runbook and tests

Typical layout:

```text
automation/examples/<task_pack>/
  shared_instructions.md
  prompts/
    pass1_task.md
    pass2_task.md
  inputs/
    pass1.input_manifest.json
    pass2.input_manifest.json
  workflows/
    <workflow>.workflow.json
  tools/
    web_search.profile.json
```

A pack may also live anywhere else under the chosen workspace root. Asset references are resolved relative to the workflow manifest, not a fixed engine-level directory.

## What The Operator Provides

The framework owns the generic mechanics. The operator provides workflow-specific runtime inputs:

- `--workflow-file`  
  Which task pack to run.

- `--primary-job-input`  
  External approved inputs that vary per run.

- `--review-bundle`  
  Approved handoff bundle from a prior review gate.

- optional runtime overrides  
  Examples: `--run-dir`, `--stage`, `--skip-token-count`, `--wait`, `--primary-model`, `--structural-model`.

The task pack itself provides the static repo corpus, fixed reference context, prompts, tool settings, and stage definitions.

## Workflow Manifest

The workflow manifest defines:

- workflow id and mode
- shared instructions file
- operator input rules
- default model roles
- request defaults
- stage order
- stage gates
- carry-forward rules
- output format and optional sidecar extraction

Supported workflow modes:

- `one_pass`
- `two_pass`
- `reviewed_three_stage`
- `custom_ordered`

Schema:

- `schemas/workflow_manifest.schema.json`

## Stage Input Manifest

Each stage input manifest defines the static attachment set for that stage:

- `primary_job_inputs`
- `reviewed_handoff_inputs`
- `attached_repository_files`
- `reference_context`

At runtime, the engine merges:

- the static stage input manifest
- operator-supplied primary job inputs
- approved review-bundle attachments
- optional carry-forward stage outputs

Schema:

- `schemas/input_manifest.schema.json`

## Review Bundle

The review bundle is the pass-to-pass approval contract.

It binds:

- workflow id
- source stage id
- source run id
- optional approved downstream handoff markdown
- approved markdown artifact
- raw response JSON
- optional structured JSON artifact
- reviewer notes
- locked decisions
- open dependencies
- artifact hashes

Schema:

- `schemas/review_bundle.schema.json`

When present, approved downstream handoff markdown is carried into the next reviewed stage ahead of the raw prior-stage artifact so reviewers can provide a concise, authoritative synthesis without discarding the detailed stage output.

## Sidecar Processing

A stage can emit text as its primary artifact and still request structured JSON as a sidecar.

When configured, the engine:

1. completes the primary stage response
2. uploads the rendered markdown artifact and raw response JSON
3. runs a structural-processing model against the sidecar schema
4. writes:
   - `output.structured.json`
   - `sidecar.response.json`
   - `sidecar.response.md`

This keeps structured extraction inside the framework instead of pushing it into custom wrapper scripts.

Sidecar artifacts are finalized only on the terminal-artifact path. In operator terms:

- `run --wait` writes the primary artifacts and the sidecar when the stage reaches terminal status.
- `resume --wait` does the same for a previously submitted background stage.
- `refresh` records the latest remote status only.

If a stage shows `response_status=completed` in `run_manifest.json` but is missing `output.structured.json` or `sidecar.response.*`, the stage has been refreshed but not finalized locally yet. Run `resume` on that stage to backfill the final artifacts.

## Artifact Layout

Default output root:

- `.local/automation/responses_runner_v2/runs`

Each run directory contains:

- `run_manifest.json`
- `stages/<NN_stage_id>/input_manifest.json`
- `stages/<NN_stage_id>/input_manifest.md`
- `stages/<NN_stage_id>/request_payload.json`
- `stages/<NN_stage_id>/uploads.json`
- `stages/<NN_stage_id>/response.latest.json`
- `stages/<NN_stage_id>/response.final.json`
- `stages/<NN_stage_id>/response.final.md`
- `stages/<NN_stage_id>/stage_checkpoint.json`
- optional `token_preflight.json` or `token_preflight.error.json`
- optional `output.structured.json`
- optional `sidecar.response.json`
- optional `sidecar.response.md`

Run manifests and checkpoints record ISO 8601 timestamps in UTC.

## Synthetic Proof Pack

The repository includes `automation/examples/responses_runner_v2_synthetic/` as the bounded proof pack for the engine itself.

It is intentionally small and synthetic so the runner can be verified without depending on a business-specific workflow. It exercises:

- one-pass execution
- automatic two-pass carry-forward
- reviewed gating with a review bundle
- sidecar extraction
- dry-run behavior
- resume and refresh behavior
- token-preflight fallback behavior

Use that pack to validate the engine and to copy the overall task-pack shape.

## External Project Use

When using this repository as a standalone tool checkout against another project:

1. keep the runner checkout wherever convenient
2. place the task pack and all referenced static assets under the target project root
3. invoke `automation/run_responses_v2.py` from this repository with `--root <target-project-root>`
4. keep review bundles and run outputs under that same target project root

Because the first release keeps one-root enforcement, it does **not** support storing the task pack in the runner checkout while pointing attachments at a different project tree.

## Recommended Reading Order

1. `docs/runbooks/responses-runner-v2.md`
2. `automation/run_responses_v2.py`
3. `automation/responses_runner_v2/contracts.py`
4. `automation/responses_runner_v2/pack_loader.py`
5. `automation/responses_runner_v2/workflow.py`
6. `automation/responses_runner_v2/attachments.py`
7. `automation/responses_runner_v2/review_bundle.py`
8. `automation/responses_runner_v2/sidecar.py`
9. `automation/examples/responses_runner_v2_synthetic/README.md`
