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
- an additive supervisor lane for end-to-end AI-operated execution after an initial clarification gate

## First-Release Operating Contract

- The runner is shipped as a standalone tool repository.
- Each invocation operates against **one exact workspace root**.
- `--root` is the exact workspace root when supplied.
- If `--root` is omitted, `RESPONSES_RUNNER_V2_ROOT` is used when set.
- If neither is supplied, the current working directory is used as-is.
- Workflow manifests, static inputs, review bundles, carry-forward artifacts, supervisor sessions, archives, and run outputs must all stay under that one root.
- Dual-root support is deliberately deferred.

## Important Packaging Note

The repository is named `staged-workflow-runner`, but the first release intentionally preserves the current internal `automation/...` layout, `responses_runner_v2` module path, CLI filenames, and schema identifiers.

That keeps the transfer close to the tested implementation and avoids unnecessary churn across:

- workflow manifests
- input manifests
- tests
- runbooks
- operator muscle memory

## Model Defaults

Runtime and committed workflow defaults use the GPT-5.5 family:

- primary generation: `gpt-5.5-pro`
- structural processing: `gpt-5.5`
- committed GPT-5.5-family prompt cache retention: `24h`
- high-stakes primary generation reasoning effort: `xhigh`
- structural processing reasoning effort: `high` or `medium`
- locked high-stakes self-improvement max output tokens: `128000`

## Generic Runner Quick Start

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

## Supervisor Lane Quick Start

Create a clarified brief, then initialize a supervisor session:

```bash
python3 automation/run_responses_supervisor_v2.py init-session \
  --root . \
  --clarified-task-brief docs/clarified_task_brief.md \
  --summary "One-sentence accepted task summary"
```

Stage a scaffold for pre-launch review:

```bash
python3 automation/run_responses_supervisor_v2.py stage-scaffold \
  --root . \
  --session <supervisor_session_id> \
  --scaffold-path automation/task_packs/example_task
```

Dry-run the scaffold before paid execution:

```bash
python3 automation/run_responses_supervisor_v2.py dry-run-scaffold \
  --root . \
  --session <supervisor_session_id> \
  --workflow-file automation/task_packs/example_task/workflows/workflow.json
```

The supervisor then runs the required review loop:

1. operator Codex provisional review via `codex exec`;
2. Codex review agent via `codex exec`;
3. Claude review agent via `claude --bare -p`;
4. deterministic consolidation;
5. operator selective acceptance with applied-change evidence.

The supervisor creates approved review bundles only after operator acceptance.

## Repository Layout

- `automation/responses_runner_v2/` — core engine package
- `automation/run_responses_v2.py` — generic runner CLI
- `automation/create_review_bundle_v2.py` — approved handoff bundle CLI
- `automation/run_responses_supervisor_v2.py` — supervised execution CLI
- `automation/run_responses_v2_eval.py` — lightweight eval and freeze-gate helper
- `automation/examples/responses_runner_v2_synthetic/` — bounded proof pack
- `automation/task_packs/responses_runner_v2_supervisor_internal/` — internal supervisor prompts and command templates
- `automation/tests/` — regression coverage
- `docs/runbooks/` — operator-facing runbooks

## Validation

Run the focused validation suite after applying supervisor changes:

```bash
python3 -m pytest \
  automation/tests/test_responses_runner_v2_model_migration.py \
  automation/tests/test_responses_runner_v2_supervisor.py \
  automation/tests/test_responses_runner_v2_contracts.py \
  automation/tests/test_responses_runner_v2_workflow.py
```

Dry-run the migrated synthetic proof pack:

```bash
python3 automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --dry-run
```

## Start Here

1. `AGENTS.md`
2. `TEAM_ONBOARDING.md`
3. `docs/runbooks/responses-runner-v2.md`
4. `automation/responses_runner_v2/README.md`
5. `automation/examples/responses_runner_v2_synthetic/README.md`
6. `automation/tests/test_responses_runner_v2_workflow.py`
