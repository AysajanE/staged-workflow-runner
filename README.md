# staged-workflow-runner

A manifest-driven runner for high-stakes staged OpenAI Responses workflows that operators can point at any single workspace root without rewriting the engine.

## What This Repository Provides

This repository packages a reusable high-stakes runner with:

- manifest-driven staged workflows
- reviewed handoff bundles
- token preflight before live submission
- upload lifecycle tracking and optional cleanup
- dry run, resume, and refresh controls
- optional structured sidecar extraction
- a bounded synthetic proof pack for validation

## First-Release Operating Contract

- The runner is shipped as a standalone tool repository.
- Each invocation operates against **one exact workspace root**.
- `--root` is the exact workspace root when supplied.
- If `--root` is omitted, `RESPONSES_RUNNER_V2_ROOT` is used when set.
- If neither is supplied, the current working directory is used as-is.
- Workflow manifests, static inputs, review bundles, carry-forward artifacts, and run outputs must all stay under that one root.
- Dual-root support is deliberately deferred.

## Important Packaging Note

The repository is named `staged-workflow-runner`, but the first release intentionally preserves the current internal `automation/...` layout, `responses_runner_v2` module path, CLI filenames, and schema identifiers.

That keeps the transfer close to the tested implementation and avoids unnecessary churn across:

- workflow manifests
- input manifests
- tests
- runbooks
- operator muscle memory

## Quick Start

Dry-run the bundled synthetic proof pack from this repository root:

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --dry-run
```

Run the same proof pack live and wait for completion:

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --wait
```

Use the runner checkout against an external target workspace:

```bash
python3 /path/to/staged-workflow-runner/automation/run_responses_v2.py run \
  --root /path/to/target-workspace \
  --workflow-file task_packs/example/workflows/example.workflow.json \
  --wait
```

Set `OPENAI_API_KEY` in the environment, or place it in `.env` under the workspace root used for the run.

## Repository Layout

- `automation/responses_runner_v2/` — core engine package
- `automation/run_responses_v2.py` — generic runner CLI
- `automation/create_review_bundle_v2.py` — approved handoff bundle CLI
- `automation/run_responses_v2_eval.py` — lightweight eval and freeze-gate helper
- `automation/examples/responses_runner_v2_synthetic/` — bounded proof pack
- `automation/tests/` — regression coverage
- `docs/runbooks/` — operator-facing runbooks

## Start Here

1. `TEAM_ONBOARDING.md`
2. `docs/runbooks/responses-runner-v2.md`
3. `automation/responses_runner_v2/README.md`
4. `automation/examples/responses_runner_v2_synthetic/README.md`
5. `automation/tests/test_responses_runner_v2_workflow.py`
