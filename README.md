# staged-workflow-runner

[![CI](https://github.com/AysajanE/staged-workflow-runner/actions/workflows/ci.yml/badge.svg)](https://github.com/AysajanE/staged-workflow-runner/actions/workflows/ci.yml)

A manifest-driven runner for staged OpenAI Responses workflows. A task pack declares an
ordered set of stages, each with its own prompt, input manifest, model role, and gate. The
runner builds each request, submits it, waits for the result, writes `artifact.md`, and moves
through the gates until a human is needed or the terminal stage is done.

## Current Status

- One engine: `automation/responses_runner_v2/`.
- One CLI: `automation/run_responses_v2.py` with subcommands `run`, `resume`, `refresh`,
  `cancel`, and `recover-uploads`.
- Stage gates: `auto`, `reviewed`, `human`, `terminal` (`review_required` is a legacy spelling
  loaded as `human`).
- Real task pack: `automation/task_packs/gstack_design_to_po_playbook/` (reviewed gates on
  stages 1-4, terminal stage 5).
- Examples: a synthetic proof pack and an offline evidence-synthesis pack under
  `automation/examples/`.
- The former supervisor lane (sessions, scaffold review, the multi-agent review loop, bundles,
  archives) was removed: on the real supervised run it cost 282 reviewer-agent minutes against
  32 minutes of primary model time and never changed the primary output.
- `run_manifest.json` is now the single durable record per run.

## Operating Contract

- Each invocation operates against one exact workspace root.
- Root resolution order: explicit `--root`, then `RESPONSES_RUNNER_V2_ROOT`, then the current
  working directory.
- Workflow manifests, input manifests, attachments, handoff notes, and run outputs must stay
  under that root. Run outputs default to `.local/automation/responses_runner_v2/runs/`.
- Task behavior belongs in task-pack files: prompts, manifests, tool profiles, and schemas.
- Workflow v2 records an assurance profile and a per-stage `max_input_tokens` budget.
- Runtime input-binding files (`--input-binding-file`) can scope a named operator input to the
  whole workflow or to an explicit stage set.

## Requirements

- Python 3.10 or newer; `jsonschema` is the only runtime dependency. Install the checkout with
  `python -m pip install -e .`.
- `OPENAI_API_KEY` in the environment, or a `.env` file in the workspace root.
- For `reviewed` gates: the `codex` CLI on `PATH`, or the `claude` CLI on `PATH` and logged in.
  Claude is invoked with API-key environment variables stripped so subscription login is used.
- Optional for tests: `pytest`. The suite also runs under standard-library `unittest`.

## Model Defaults

Committed workflows use the GPT-5.6 alias family:

- primary generation: `gpt-5.6`, `reasoning_effort=xhigh`, `reasoning_mode=pro`
- structural processing: `gpt-5.6`, standard reasoning mode, `high` or `medium` effort
- prompt caching: `prompt_cache_mode=implicit`, `prompt_cache_ttl=30m`; stable per-lane cache
  keys by default (`--prompt-cache-key-strategy legacy_stage_v1` for paired A/B comparison)
- context window `1050000`; maximum output tokens `128000`
- reviewers: `codex` defaults to `gpt-5.6-sol` at `high` effort; `claude` defaults to `opus`
  at `xhigh` effort

## Quick Start

Run the tests:

```bash
python -m unittest discover -s automation/tests -p 'test_*.py'
```

Dry-run the synthetic proof pack:

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --dry-run
```

Dry-run every stage of the gstack playbook pack with a primary input. This is the whole
pre-launch check: without `--stage`, `--dry-run` renders every stage's `request_payload.json`,
`input_manifest.json`, and `input_manifest.md` under `<run_dir>/dry_runs/`, using placeholder
files under `dry_runs/stubs/` for handoffs from stages that have not run yet.

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json \
  --primary-job-input docs/runbooks/first-use-adaptation-example.md \
  --dry-run
```

Run live. `run` waits in-process by default (polling every 20 s) and chains through `auto` and
`reviewed` gates, including revisions, until a `human` gate, a blocked review, the terminal
stage, or an error. Use `--no-wait` to return right after submission and finish later with
`resume --run-dir <run_dir> --stage <stage_id>`.

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json \
  --primary-job-input docs/gstack/<approved-design-or-brief>.md
```

Useful `run` flags: `--reviewer {codex,claude,none}` overrides the workflow's reviewer for this
invocation; `--skip-token-count` disables the exact token preflight; `--run-dir` continues an
existing run; `--handoff-note` is described below.

## Stage Gates

Each stage declares a `gate` in the workflow JSON.

- `auto`: the run continues to the next stage.
- `reviewed`: when the stage completes, one reviewer CLI reads `artifact.md`, the stage task
  text, the input manifest, and the handoff inputs, and returns a JSON verdict
  (`approve` or `revise`, with a summary, blocking findings, and notes). On `approve` the run
  continues. On `revise` the engine runs one revision attempt of the same stage
  (`attempt_002`) with the reviewer notes and previous draft attached, then reviews again. A
  second `revise` leaves the stage at `waiting_for_review` with `review_status: blocked` until
  a human supplies `--handoff-note`. If the reviewer CLI fails, the stage stays completed with
  its review pending and the next `run` retries it. Review evidence lands in the attempt's
  `review/` directory (`verdict.json`, `reviewer_notes.md`, prompt, stdout, stderr, and an
  invocation record with duration, exit code, and any reported cost or token fields).
- `human`: the stage completes and the run stops with the stage at `waiting_for_review`. Read
  `artifact.md`, write a note, and continue; the note is attached to the next stage together
  with the artifact:

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file <workflow.json> \
  --run-dir <run_dir> \
  --handoff-note <note.md>
```

- `terminal`: the last stage; nothing runs after it and `artifact.md` is the deliverable.
- `review_required` (legacy spelling): loaded as `human`.

Review behavior is configured by workflow `defaults.review` and per-stage `review`: `reviewer`
(`codex` default, `claude`, or `none`), `model`, `effort`, `timeout_seconds` (default 1800),
and `max_revisions` (default 1). The verdict schema is
`automation/responses_runner_v2/schemas/stage_review_verdict.schema.json` and the reviewer
prompt is `automation/responses_runner_v2/prompts/stage_review.md`.

Stages pass work forward through `carry_forward`: `handoff_from_stage_id` attaches an earlier
reviewed or human stage's approved artifact plus its reviewer notes or human note as Reviewed
Handoff Inputs; `reference_context_from_stage_ids` attaches earlier artifacts as Reference
Context; `review_bundle_from_stage_id` is accepted only as a legacy alias for
`handoff_from_stage_id`.

Token preflight: the exact count (`POST /responses/input_tokens`) is the only token check; it
blocks when the count exceeds the stage `max_input_tokens`, and `--skip-token-count` disables
it. Post-output validators such as `evidence_references_v1` are also advisory: results go to
`validator_report.json` and the manifest fields `validators_passed` and
`validator_report_path`; a failed validator never blocks finalization.

## Repository Layout

- `AGENTS.md` — repository-level automation-agent instructions.
- `DEVELOPING.md` — developer guide and architecture guardrails.
- `automation/responses_runner_v2/` — engine package (`workflow.py`, `reviewer.py`,
  `contracts.py`, `validators.py`, schemas, prompts).
- `automation/run_responses_v2.py` — the runner CLI.
- `automation/run_responses_v2_eval.py` — deterministic eval and freeze-gate helper over
  `automation/evals/`.
- `automation/examples/responses_runner_v2_synthetic/` — bounded proof pack.
- `automation/examples/responses_runner_v2_evidence_synthesis/` — offline, non-coding evidence
  and citation example.
- `automation/task_packs/gstack_design_to_po_playbook/` — five-stage lane for drafting
  reviewed `markdown_playbook_v1` playbooks from gstack planning inputs.
- `automation/tests/` — regression tests.
- `docs/runbooks/` — operator-facing runbooks.
- `docs/design/` — architecture walkthrough.
- `pyproject.toml` — packaging metadata and console-script entry points.

## Publication Boundary

Push these to GitHub:

- engine code, CLIs, schemas, prompts, tests, eval fixtures, examples, runbooks, and task-pack
  definitions;
- `AGENTS.md`, `DEVELOPING.md`, `CONTRIBUTING.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`,
  `CHANGELOG.md`, `LICENSE`, and this `README.md`.

Do not push these:

- `.env` or any environment-specific secret file;
- `.local/` run outputs, response artifacts, review evidence, or handoff notes;
- `.pytest_cache/`, `__pycache__/`, `*.pyc`, `.DS_Store`, and scratch directories such as
  `inspect_live.*`;
- project-specific handoff material for unrelated target repositories.

## Validation

CI (`.github/workflows/ci.yml`) runs these on Python 3.10, 3.11, and 3.12:

```bash
python -m pip install -e .

python -m unittest discover -s automation/tests -p 'test_*.py'

python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json \
  --dry-run

python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json \
  --primary-job-input docs/runbooks/first-use-adaptation-example.md \
  --dry-run
```

Optional pytest run:

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest automation/tests -q
```

## License

This project is licensed under the MIT License. See `LICENSE`.

## Start Here

1. `AGENTS.md`
2. `DEVELOPING.md`
3. `docs/runbooks/responses-runner-v2.md`
4. `automation/responses_runner_v2/README.md`
5. `automation/task_packs/gstack_design_to_po_playbook/README.md`
6. `automation/tests/test_responses_runner_v2_reviewed_gates.py`
