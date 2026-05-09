# staged-workflow-runner

[![CI](https://github.com/AysajanE/staged-workflow-runner/actions/workflows/ci.yml/badge.svg)](https://github.com/AysajanE/staged-workflow-runner/actions/workflows/ci.yml)

A manifest-driven runner for high-stakes staged OpenAI Responses workflows. The runner is designed to execute complex tasks from explicit task packs, preserve durable local evidence, support reviewed handoffs between stages, and optionally operate through an additive supervisor lane.

## Current Status

This repository is ready to publish as a standalone source repository for the runner.

- Core engine: implemented under `automation/responses_runner_v2/`.
- Generic CLI: `automation/run_responses_v2.py`.
- Review-bundle CLI: `automation/create_review_bundle_v2.py`.
- Supervisor CLI: `automation/run_responses_supervisor_v2.py`.
- Synthetic proof pack: included under `automation/examples/responses_runner_v2_synthetic/`.
- Supervisor/self-improvement packs: included under `automation/task_packs/`.
- Local run outputs, secrets, caches, and scratch archives are intentionally excluded from Git.

The first release intentionally preserves the tested internal layout and names: `automation/...`, `responses_runner_v2`, existing CLI filenames, and schema identifiers.

## Operating Contract

- Each invocation operates against one exact workspace root.
- Root resolution order is explicit `--root`, then `RESPONSES_RUNNER_V2_ROOT`, then the current working directory.
- Workflow manifests, input manifests, static attachments, review bundles, supervisor sessions, archives, and run outputs must stay under that root.
- Dual-root mode is deliberately deferred for the first release.
- Task behavior belongs in task-pack files: prompts, manifests, tool profiles, schemas, and reviewed handoff bundles.

## Requirements

- Python 3.10 or newer.
- `OPENAI_API_KEY` in the environment, or a `.env` file in the active workspace root for live OpenAI runs.
- For local development, create a Python 3.10+ environment and install this checkout with `python -m pip install -e .`.
- No mandatory third-party runtime package for the core runner; the HTTP client uses the Python standard library.
- Optional: `jsonschema` for full JSON Schema validation. A limited fallback validator is built in for supervisor artifacts.
- Optional for tests: `pytest`. The repository test suite also runs with standard-library `unittest`.
- Supervisor review automation additionally requires:
  - Codex CLI available as `codex`, installed and authenticated according to [OpenAI Codex CLI documentation](https://help.openai.com/en/articles/11096431-openai-codex-ci-getting-started);
  - Claude Code CLI available as `claude`, installed according to [Anthropic Claude Code documentation](https://docs.anthropic.com/en/docs/claude-code/overview) and authenticated once before use. Subscription-authenticated review uses non-bare `claude -p` because `--bare` skips OAuth/keychain credentials;
  - non-interactive command execution available in the current shell.

## Model Defaults

Runtime and committed workflow defaults use the GPT-5.5 family:

- primary generation: `gpt-5.5-pro`
- structural processing: `gpt-5.5`
- committed GPT-5.5-family prompt cache retention: `24h`
- high-stakes primary reasoning effort: `xhigh`
- structural reasoning effort: `high` or `medium`
- locked high-stakes self-improvement max output tokens: `128000`

## Quick Start

Run the full local test suite:

```bash
python -m unittest discover -s automation/tests -p 'test_*.py'
```

Dry-run the bundled synthetic proof pack:

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --dry-run
```

Run the same proof pack live and wait for completion:

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --skip-token-count \
  --wait
```

Use this checkout against an external target workspace:

```bash
python /path/to/staged-workflow-runner/automation/run_responses_v2.py run \
  --root /path/to/target-workspace \
  --workflow-file task_packs/example/workflows/example.workflow.json \
  --skip-token-count \
  --wait
```

## Supervisor Lane

The supervisor lane is additive: it does not replace the generic runner engine. The engine still owns workflow loading, request construction, Responses API submission, resume/refresh, artifact finalization, sidecar extraction, and review-bundle validation.

The supervisor owns session state, scaffold staging, dry-run gating, review-agent invocation, consolidation, operator selective acceptance, failure classification, archive-before-rerun evidence, human-pause records, and final implementation-bundle assembly.

Initialize a session after a human has accepted a clarified task brief:

```bash
python automation/run_responses_supervisor_v2.py init-session \
  --root . \
  --clarified-task-brief docs/clarified_task_brief.md \
  --summary "One-sentence accepted task summary"
```

Stage, statically examine, and then executable-dry-run a scaffold:

```bash
python automation/run_responses_supervisor_v2.py stage-scaffold \
  --root . \
  --session <supervisor_session_id> \
  --scaffold-path automation/task_packs/example_task

python automation/run_responses_supervisor_v2.py examine-scaffold \
  --root . \
  --session <supervisor_session_id> \
  --workflow-file automation/task_packs/example_task/workflows/workflow.json

python automation/run_responses_supervisor_v2.py dry-run-scaffold \
  --root . \
  --session <supervisor_session_id> \
  --workflow-file automation/task_packs/example_task/workflows/workflow.json \
  --primary-job-input docs/accepted_primary_input.md
```

`examine-scaffold` is the pre-launch static scaffold review gate. It validates the workflow scaffold, resolves static task-pack attachments, checks model posture, stage-gate shape, sidecar schema compatibility, tool profiles, and stage prompt/input inventory without constructing a Stage 1 request. `dry-run-scaffold` remains the executable request-construction gate and therefore accepts the same runtime inputs that a real Stage 1 run would require.

For every scaffold and non-terminal stage, the required supervisor review loop is:

1. operator Codex provisional review through `codex exec`;
2. independent read-only Codex review through `codex exec`;
3. independent read-only Claude review through subscription-authenticated `claude -p`;
4. deterministic consolidation;
5. operator selective acceptance with applied-change evidence;
6. approved review-bundle creation only after acceptance.

## Repository Layout

- `AGENTS.md` — repository-level automation-agent instructions.
- `DEVELOPING.md` — developer guide and architecture guardrails.
- `automation/responses_runner_v2/` — core engine package.
- `automation/run_responses_v2.py` — generic runner CLI.
- `automation/create_review_bundle_v2.py` — approved review-bundle CLI.
- `automation/run_responses_supervisor_v2.py` — supervisor CLI.
- `automation/run_responses_v2_eval.py` — lightweight eval and freeze-gate helper.
- `automation/examples/responses_runner_v2_synthetic/` — bounded proof pack.
- `automation/task_packs/responses_runner_v2_supervisor_internal/` — supervisor prompt and command-template library.
- `automation/task_packs/responses_runner_v2_supervised_end_to_end/` — current four-stage self-improvement pack.
- `automation/task_packs/responses_runner_v2_supervisory_lane/` — legacy three-stage supervisory-lane pack kept as historical regression coverage.
- `automation/tests/` — regression tests.
- `docs/runbooks/` — operator-facing runbooks.
- `docs/design/supervised-self-improvement-pack.md` — public design summary for the current supervised self-improvement pack.
- `pyproject.toml` — packaging metadata and console-script entry points.

## Publication Boundary

Push these to GitHub:

- core runner code, CLIs, schemas, tests, eval fixtures, synthetic examples, runbooks, and task-pack definitions;
- `AGENTS.md`, `DEVELOPING.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `CHANGELOG.md`, `LICENSE`, and this `README.md`;
- supervisor internal prompt and command-template assets, because the supervisor CLI depends on them.

Do not push these:

- `.env` or any environment-specific secret file;
- `.local/` run outputs, response artifacts, supervisor sessions, archives, extracted packets, or internal handoffs;
- `.pytest_cache/`, `__pycache__/`, `*.pyc`, `.DS_Store`, and scratch directories such as `inspect_live.*`;
- project-specific handoff material for unrelated target repositories;
- local design-provenance drafts that are archived under ignored `.local/internal_archive/`.

Project-specific handoff runbooks that were useful during local development have been moved out of the publishable tree and preserved under ignored `.local/internal_archive/`.

## Validation

Baseline validation:

```bash
python -m unittest discover -s automation/tests -p 'test_*.py'
```

Optional pytest validation:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest automation/tests -q
```

Dry-run validation:

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --dry-run

python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/responses_runner_v2_supervised_end_to_end/workflows/four_stage.workflow.json \
  --dry-run
```

Supervisor smoke:

```bash
python automation/run_responses_supervisor_v2.py validate-session \
  --root . \
  --session <supervisor_session_id>
```

CI runs the `unittest` suite and both dry-run smokes on Python 3.10, 3.11, and 3.12.

## License

This project is licensed under the MIT License. See `LICENSE`.

## Start Here

1. `AGENTS.md`
2. `DEVELOPING.md`
3. `docs/runbooks/responses-runner-v2.md`
4. `automation/responses_runner_v2/README.md`
5. `automation/examples/responses_runner_v2_synthetic/README.md`
6. `automation/tests/test_responses_runner_v2_workflow.py`
