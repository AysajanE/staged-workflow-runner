# Team Onboarding Note

This repository is the extracted standalone home for the staged workflow runner.

The quickest correct mental model is:

1. The engine is generic.
2. The workflow definition lives in task-pack files.
3. Every run is anchored to one exact workspace root.
4. The runner writes durable artifacts under that same root.

## What This Repo Is

`staged-workflow-runner` is a manifest-driven framework for staged OpenAI Responses workflows with:

- workflow manifests
- stage input manifests
- reviewed handoff bundles
- token preflight
- upload lifecycle handling
- dry run, resume, and refresh
- optional structured sidecar extraction

The first release deliberately preserves the internal `automation/...` layout and `responses_runner_v2` package path. Do not treat that as accidental leftover structure.

## First Things To Understand

These are the most important operating constraints:

1. One exact workspace root per run.
   `--root` wins, then `RESPONSES_RUNNER_V2_ROOT`, then the current working directory.
2. All workflow assets and run artifacts must stay under that one root.
   That includes workflow manifests, static inputs, review bundles, uploaded attachments, and `.local/automation/...` run output.
3. The runner checkout and the target workspace can be the same repo or different repos.
   In first release, the task pack still has to live under the chosen workspace root.
4. The public repo name changed, but the tested engine contract did not.
   The internal package name, CLI names, and schema identifiers are intentionally preserved.

## Read In This Order

1. `README.md`
2. `docs/runbooks/responses-runner-v2.md`
3. `automation/responses_runner_v2/README.md`
4. `automation/examples/responses_runner_v2_synthetic/README.md`
5. `automation/tests/test_responses_runner_v2_workflow.py`

Then read the engine in this order:

1. `automation/responses_runner_v2/contracts.py`
2. `automation/responses_runner_v2/pack_loader.py`
3. `automation/responses_runner_v2/workflow.py`
4. `automation/responses_runner_v2/attachments.py`
5. `automation/responses_runner_v2/artifacts.py`
6. `automation/responses_runner_v2/review_bundle.py`
7. `automation/responses_runner_v2/sidecar.py`
8. `automation/responses_runner_v2/openai_client.py`

## Core Runtime Objects

- Workflow manifest:
  Declares stage order, model roles, request defaults, review gates, carry-forward rules, and optional sidecar extraction.
- Stage input manifest:
  Declares the static attachment set for a stage.
- Review bundle:
  The approval contract passed between reviewed stages.
- Run manifest and stage checkpoints:
  The durable local execution record for resumes, refreshes, and audits.

## Authority Order

Stage attachments are assembled in a fixed authority order:

1. Primary Job Inputs
2. Reviewed Handoff Inputs
3. Attached Repository Files
4. Reference Context

If something looks wrong in a stage request, inspect the rendered stage `input_manifest.md` first.

## Fastest Way To Verify The Engine

Start with the synthetic proof pack. It is intentionally the smallest bounded pack that still exercises the framework shape.

Dry run:

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --dry-run
```

Core regression suite:

```bash
python3 -m unittest \
  automation.tests.test_responses_runner_v2_contracts \
  automation.tests.test_responses_runner_v2_example_pack \
  automation.tests.test_responses_runner_v2_review_bundle \
  automation.tests.test_responses_runner_v2_workflow \
  automation.tests.test_responses_runner_v2_eval
```

Eval harness smoke test:

```bash
python3 automation/run_responses_v2_eval.py \
  --dataset-file automation/evals/responses_runner_v2.eval.json \
  --list-cases
```

## What Not To Reopen Casually

These are deliberate first-release choices, not unfinished cleanup:

- standalone repo plus one exact workspace root per run
- preserved `automation/...` layout
- preserved `responses_runner_v2` package path
- preserved CLI filenames
- preserved schema identifiers
- attached-evidence-first posture
- dual-root support deferred

If you change one of those, treat it as an explicit product and compatibility decision, not a refactor.

## Working Norms

- Use the synthetic pack to prove framework changes before touching any business-specific pack.
- Keep changes generic at the engine boundary unless you are intentionally authoring a task pack.
- Prefer updating tests and runbooks with engine changes so the generic contract stays obvious.
- Do not commit `.local/` run artifacts.
- Expect reviewed stages and review bundles to be part of the normal workflow, not exceptional machinery.

## If You Need One File To Start

Start with `automation/responses_runner_v2/workflow.py`.

That file shows how the framework resolves roots, stages attachments, applies review gates, runs token preflight, submits requests, resumes polling, and finalizes artifacts.
