# staged-workflow-runner

[![CI](https://github.com/AysajanE/staged-workflow-runner/actions/workflows/ci.yml/badge.svg)](https://github.com/AysajanE/staged-workflow-runner/actions/workflows/ci.yml)

A manifest-driven runner for high-stakes staged OpenAI Responses workflows. It supports
important coding, business, research, and operational tasks from explicit task packs, preserves
durable local evidence, supports reviewed handoffs, and optionally operates through an additive
supervisor lane.

## Current Status

This repository is ready to publish as a standalone source repository for the runner.

- Core engine: implemented under `automation/responses_runner_v2/`.
- Generic CLI: `automation/run_responses_v2.py`.
- Review-bundle CLI: `automation/create_review_bundle_v2.py`.
- Supervisor CLI: `automation/run_responses_supervisor_v2.py`.
- Synthetic proof pack: included under `automation/examples/responses_runner_v2_synthetic/`.
- Offline document-evidence example: included under `automation/examples/responses_runner_v2_evidence_synthesis/`.
- Supervisor/self-improvement packs and the high-stakes gstack-to-PO playbook lane: included under `automation/task_packs/`.
- Local run outputs, secrets, caches, and scratch archives are intentionally excluded from Git.

The first release intentionally preserves the tested internal layout and names: `automation/...`, `responses_runner_v2`, existing CLI filenames, and schema identifiers.

## Operating Contract

- Each invocation operates against one exact workspace root.
- Root resolution order is explicit `--root`, then `RESPONSES_RUNNER_V2_ROOT`, then the current working directory.
- Workflow manifests, input manifests, static attachments, review bundles, supervisor sessions, archives, and run outputs must stay under that root.
- Dual-root mode is deliberately deferred for the first release.
- Task behavior belongs in task-pack files: prompts, manifests, tool profiles, schemas, and reviewed handoff bundles.
- Workflow v2 records an assurance profile. Existing packs use `critical` and declare a
  per-stage `max_input_tokens` budget.
- Runtime input-binding files can scope a named operator input to the whole workflow or to an
  explicit stage set.

## Requirements

- Python 3.10 or newer.
- `OPENAI_API_KEY` in the environment, or a `.env` file in the active workspace root for live OpenAI runs.
- For local development, create a Python 3.10+ environment and install this checkout with `python -m pip install -e .`.
- `jsonschema` is a core dependency so persisted and task-pack contracts are validated with
  Draft 2020-12 before dataclass coercion.
- Optional for tests: `pytest`. The repository test suite also runs with standard-library `unittest`.
- Supervisor review automation additionally requires:
  - Codex CLI available as `codex`, installed and authenticated according to [OpenAI Codex CLI documentation](https://help.openai.com/en/articles/11096431-openai-codex-ci-getting-started);
  - Claude Code CLI available as `claude`, installed according to [Anthropic Claude Code documentation](https://docs.anthropic.com/en/docs/claude-code/overview) and authenticated once before use. Subscription-authenticated review uses non-bare `claude -p` because `--bare` skips OAuth/keychain credentials;
  - non-interactive command execution available in the current shell.

## Model Defaults

Runtime and committed workflow defaults use the durable GPT-5.6 alias:

- primary generation: `gpt-5.6`, `reasoning.mode=pro`
- structural processing: `gpt-5.6`, standard reasoning mode
- prompt caching: `prompt_cache_options={"mode":"implicit","ttl":"30m"}`
- prompt-cache routing: stable keys per workflow/version/model role; `legacy_stage_v1` remains available for paired A/B measurement
- high-stakes primary reasoning effort: `xhigh`
- structural reasoning effort: `high` or `medium`
- context window: `1050000`; maximum output tokens: `128000`
- locked high-stakes self-improvement max output tokens: `128000`

Changing stage verbosity or moving terminal stages between `high` and `xhigh` remains an
A/B-tested optimization, not part of the model migration.

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

Validate the non-coding evidence-synthesis example entirely offline:

```bash
python -m unittest automation.tests.test_responses_runner_v2_evidence_synthesis_example
```

Its checked-in binding file can be supplied to a real run with
`--input-binding-file automation/examples/responses_runner_v2_evidence_synthesis/runtime_input_bindings.example.json`.
The supervisor accepts the same binding file for scaffold dry runs, accepted launches, and
archive-authorized reruns, so its stage scope is preserved by the frozen run contract.

Run the same proof pack live and wait for completion:

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --wait
```

Use this checkout against an external target workspace:

```bash
python $KEEL_ROOT/tools/staged-workflow-runner/automation/run_responses_v2.py run \
  --root /path/to/target-workspace \
  --workflow-file task_packs/example/workflows/example.workflow.json \
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

For a registered non-terminal stage, the concise path derives and classifies all stage evidence:

```bash
python automation/run_responses_supervisor_v2.py review-cycle \
  --root . --session <session_id> --review-cycle <cycle_id> \
  --run-dir <run_dir> --stage <stage_id>

python automation/run_responses_supervisor_v2.py accept \
  --root . --session <session_id> --review-cycle <cycle_id> \
  --then-bundle --then-launch
```

The second command stops without bundling or launching if acceptance remains blocked. Use
`release-reservation` only when a read-only review invocation crashed before producing any
decision candidate. Re-staging byte-identical content as the latest scaffold is idempotent and
preserves its existing review state.

## Repository Layout

- `AGENTS.md` — repository-level automation-agent instructions.
- `DEVELOPING.md` — developer guide and architecture guardrails.
- `automation/responses_runner_v2/` — core engine package.
- `automation/run_responses_v2.py` — generic runner CLI.
- `automation/create_review_bundle_v2.py` — approved review-bundle CLI.
- `automation/run_responses_supervisor_v2.py` — supervisor CLI.
- `automation/run_responses_v2_eval.py` — deterministic eval and freeze-gate helper; representative cases keep hash-bound inputs/gold separate from offline runner candidate artifacts.
- `automation/examples/responses_runner_v2_synthetic/` — bounded proof pack.
- `automation/examples/responses_runner_v2_evidence_synthesis/` — offline, non-coding evidence and citation example.
- `automation/responses_runner_v2/schemas/final_delivery_bundle.schema.json` — domain-neutral terminal delivery contract; implementation packets remain the stricter implementation-specific extension.
- `automation/task_packs/responses_runner_v2_supervisor_internal/` — supervisor prompt and command-template library.
- `automation/task_packs/responses_runner_v2_supervised_end_to_end/` — current four-stage self-improvement pack.
- `automation/task_packs/responses_runner_v2_supervisory_lane/` — legacy three-stage supervisory-lane pack kept as historical regression coverage.
- `automation/task_packs/gstack_design_to_po_playbook/` — high-stakes five-stage lane for drafting reviewed `markdown_playbook_v1` playbooks from gstack planning inputs.
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

python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json \
  --primary-job-input docs/gstack/<approved-design-or-brief>.md \
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
