# Responses Runner V2

Day-to-day operation of the staged workflow engine. One CLI drives everything:
`automation/run_responses_v2.py` with the subcommands `run`, `resume`, `refresh`, `cancel`,
and `recover-uploads`.

The separate supervisor lane (sessions, scaffold review cycles, the three-agent review loop,
consolidation, acceptance, bundles) was removed: on the one real supervised run it spent
282 reviewer-agent minutes against 32 minutes of primary model time and never changed the
primary output. Stage review is now a single reviewer CLI call inside the engine.

## Prerequisites

- Python 3.10 or newer, with `jsonschema` installed (`python -m pip install -e .`)
- `OPENAI_API_KEY` in the environment, or a `.env` file in the workspace root
- a workflow manifest and every statically referenced asset under one workspace root
- for `reviewed` gates: the `codex` CLI, or the `claude` CLI already logged in, on `PATH`

## Workspace Root Contract

Every invocation operates against one exact workspace root, resolved in this order:

1. explicit `--root`
2. `RESPONSES_RUNNER_V2_ROOT`
3. the current working directory as-is

`--workflow-file`, `--primary-job-input`, `--reference-context`, `--handoff-note`,
`--input-binding-file`, `--run-dir`, and `--output-root` must all stay under that root. Run directories default to `.local/automation/responses_runner_v2/runs/`. There is
no dual-root mode.

## Model Defaults

- primary generation: durable alias `gpt-5.6` with `reasoning_mode=pro`
- structural processing: durable alias `gpt-5.6` with standard reasoning mode
- prompt caching: `prompt_cache_mode=implicit` with `prompt_cache_ttl=30m`
- prompt-cache keys: `stable_lane_v1` by default; `--prompt-cache-key-strategy legacy_stage_v1`
  only for paired comparison
- `gpt-5.6` context window `1_050_000`, max output `128000`

The workflow loader rejects `gpt-5.6` profiles that omit the 30-minute cache TTL or use an
unsupported reasoning mode. `--primary-model` and `--structural-model` override per run.

## Preflight And Context Budget

One check runs before submission: the **exact count** (`POST /responses/input_tokens`). The
create payload is projected onto the fields the count endpoint accepts, and the result is
written to `token_preflight.json` (or `token_preflight.error.json` when it blocks). It fails
closed when the count exceeds the stage's `max_input_tokens` (or `--max-input-tokens`) or the
model context window minus the requested output and a safety margin, leaving the stage
`blocked_preflight`; fix the input and rerun that stage in place with
`run --run-dir <run_dir> --stage <stage_id>`. There is no local estimate.

`--skip-token-count` disables the exact count. Keep it enabled for real work.

## Dry Run Every Stage

`run --dry-run` without `--stage` renders every stage of the workflow, in order, under
`<run_dir>/dry_runs/stages/NN_<stage_id>/`: `input_manifest.json`, `input_manifest.md`,
`request_payload.json`, and the `upload_inputs/` staging directory. Handoffs from stages that
have not run yet are satisfied by placeholder files under `<run_dir>/dry_runs/stubs/<stage_id>/`.
This is the whole pre-launch check.

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json \
  --primary-job-input docs/runbooks/first-use-adaptation-example.md \
  --dry-run
```

CI runs exactly this for the gstack pack and the synthetic one-pass workflow. Add `--stage
<stage_id>` to render one stage only.

## Live Run

`run` and `resume` wait in-process by default, polling every 20 seconds (`--poll-interval`,
`--max-wait-seconds`; the default ceiling is 24 hours). One waiting invocation chains through
`auto` and `reviewed` gates, including revisions, until it reaches a `human` gate, a blocked
review, the terminal stage, or an error.

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json \
  --primary-job-input docs/gstack/<approved-design-or-brief>.md
```

`--no-wait` returns right after submission; finish the stage later with `resume`. With
`--no-wait` or `--stage`, the run stops after that one stage.

`run` prints `RUN_DIR <path>` to stderr as soon as the run directory is known. After `run` or
`resume` it prints `RUN <run_status> stage <stage_id> <stage_status>`; when the run ended
`failed`, `cancelled`, `blocked`, `submission_outcome_unknown`, or `pending_finalization`, or
the stage ended `failed`, `failed_complete`, `failed_no_artifact`, `cancelled`, `incomplete`,
`blocked`, `blocked_preflight`, or `submission_outcome_unknown`, it adds a `WARNING` with the
rerun command and exits with code 2. A run stopped at `waiting_for_review` prints a `WAITING`
hint naming `--handoff-note`. Dry runs print no `RUN` line.

Resume a stage with a recorded response (`submitted`, `in_progress`,
`remote_terminal_pending_finalization`, `cancelling`, or `finalized`; waits by default;
`--no-wait` records the current status and returns):

```bash
python automation/run_responses_v2.py resume --root . --run-dir <run_dir> --stage <stage_id>
```

Refresh remote status only, never finalizing:

```bash
python automation/run_responses_v2.py refresh --root . --run-dir <run_dir> --stage <stage_id>
```

Cancel a known live response once, then finalize its local evidence:

```bash
python automation/run_responses_v2.py cancel --root . --run-dir <run_dir> --stage <stage_id>
```

To continue a stopped run, pass `--run-dir <run_dir>` to `run` with the same `--workflow-file`
(its SHA-256 must match the `workflow_manifest_sha256` recorded in `run_manifest.json`).

If the runner checkout is separate from the target project, point `--root` at the project;
`--workflow-file` and every other path are then interpreted under that root, and `.env` is read
from there too.

## Stage Gates

Each stage declares a `gate` in the workflow manifest:

| gate | what happens when the stage completes |
|---|---|
| `auto` | the run continues to the next stage |
| `reviewed` | one reviewer CLI judges `artifact.md`; approve continues, revise triggers one revision, a second revise blocks |
| `human` | the run stops with the stage `waiting_for_review` until you pass `--handoff-note` |
| `terminal` | last stage; nothing runs after it; `artifact.md` is the deliverable |

A workflow that still spells a gate `review_required` is loaded as `human`.

### Review configuration

`defaults.review` in the workflow, overridable per stage with a stage-level `review` block:

| key | values | default |
|---|---|---|
| `reviewer` | `codex`, `claude`, `none` | `codex` |
| `model` | reviewer model name | `gpt-5.6-sol` for codex, `opus` for claude |
| `effort` | `low`, `medium`, `high`, `xhigh`, `max` | `high` for codex, `xhigh` for claude |
| `timeout_seconds` | positive integer | `1800` |
| `max_revisions` | zero or more | `1` |

`run --reviewer {codex,claude,none}` overrides the reviewer for that invocation. `none`
records `review_status: not_required` and continues without invoking anything.

### What the reviewer sees and returns

The reviewer reads `artifact.md`, the stage task text, `input_manifest.md`, the stage's
reviewed handoff inputs, and `validator_report_path` when a validator report exists (prompt:
`automation/responses_runner_v2/prompts/stage_review.md`). It may also open any file listed in
`input_manifest.md` to spot-check a material claim, and nothing else. It returns one JSON
object validated against
`automation/responses_runner_v2/schemas/stage_review_verdict.schema.json`:
`{verdict: approve|revise, summary, blocking_findings[], notes[]}`.

### Revise, blocked, and the handoff note

- **approve**: `review_status: approved`; the run continues.
- **revise** (first time): `review_status: revision_requested`, stage status
  `revision_requested`. The engine runs one more attempt of the same stage (`attempt_002`,
  recorded as `attempts[].revision_of_attempt_id`) with the previous draft and the reviewer notes
  attached under Reviewed Handoff Inputs and `contracts.REVISION_INSTRUCTIONS` prefixed to the
  task. The reviewer runs again on the revision.
- **revise** beyond `max_revisions`: `review_status: blocked`, stage status
  `waiting_for_review`. The run stops. Read the artifact and the reviewer notes, then either
  approve it yourself with `--handoff-note` or fix the pack and start a new run.

A `human` gate stops the same way. Continue either case with:

```bash
python automation/run_responses_v2.py run \
  --root . \
  --workflow-file <workflow.json> \
  --run-dir <run_dir> \
  --handoff-note notes/<stage_id>.md
```

The note sets `review_status: human_approved` and `handoff_note_path`, and is attached to the
next stage next to the artifact. When no stage is waiting, the same flag approves a
`completed` reviewed stage whose review is still pending after a reviewer failure.

### Carry-forward

A later stage's `carry_forward` block may name:

- `handoff_from_stage_id`: attaches that stage's approved `artifact.md` plus its reviewer
  notes or human note as Reviewed Handoff Inputs; the source must use a `reviewed` or `human`
  gate
- `reference_context_from_stage_ids`: earlier artifacts as Reference Context
- `review_bundle_from_stage_id`: accepted only as a legacy alias for `handoff_from_stage_id`;
  naming both with different values is rejected at load time

### Where review evidence lives

Under the attempt directory, `stages/NN_<stage_id>/attempt_NNN/review/`:

- `verdict.json` (normalized verdict plus `disposition`, reviewer, artifact hash)
- `reviewer_notes.md` (markdown rendering that later stages receive)
- `prompt_<stamp>.md`, `stdout_<stamp>.txt`, `stderr_<stamp>.txt`
- `invocation_<stamp>.json` (argv, duration, exit code, cost or token fields when the CLI
  reports them)

The run manifest stage summary carries `review_status`, `reviewer_notes_path`,
`review_verdict_path`, `handoff_note_path`, `validators_passed`, and `validator_report_path`.
Post-output validators are advisory: a failed validator is recorded in `validator_report.json`
and printed as a warning, and never blocks finalization.

The reviewer invocation and its manifest update run under the run lock, so a second `run` on
the same run directory during a review is refused ("Run is locked by another process"). If
the reviewer CLI exits non-zero, times out, cannot be spawned, or returns output without a
valid verdict, the stage stays `completed` with its review pending and the command exits with
an error naming the continuation: rerun the same command with `--run-dir <run_dir>` to retry
the review; add `--handoff-note <note.md>` to approve the artifact yourself (recorded as
`review_status: human_approved` with `handoff_note_path`); or add `--reviewer none`. A bare
`run` without `--run-dir` starts a NEW run and resubmits the stage at full cost. A codex
verdict is rejected into the same pending, retryable state when the codex transcript never
opened the artifact path. A crash between completion and review leaves the same state;
`run --run-dir <run_dir>` applies the pending review first, then continues (`resume` refuses a
`completed` stage).

## Reviewer Command Shapes

As built by `automation/responses_runner_v2/reviewer.py`. Both run with the workspace root as
the working directory.

Codex, prompt plus review job on stdin:

```bash
codex exec --sandbox read-only --ephemeral --ignore-user-config \
  -c 'model_reasoning_effort="high"' \
  --output-schema automation/responses_runner_v2/schemas/stage_review_verdict.schema.json \
  --model gpt-5.6-sol -
```

Claude, review job JSON on stdin, prompt supplied as the appended system prompt:

```bash
env -u ANTHROPIC_API_KEY -u ANTHROPIC_AUTH_TOKEN -u CLAUDE_CODE_OAUTH_TOKEN \
    -u CLAUDE_CODE_USE_BEDROCK -u CLAUDE_CODE_USE_VERTEX -u CLAUDE_CODE_USE_FOUNDRY \
  claude -p --model opus --effort xhigh --output-format json \
  --tools Read,Grep,Glob --permission-mode dontAsk --no-session-persistence \
  --setting-sources user \
  --append-system-prompt-file <attempt_dir>/review/prompt_<stamp>.md
```

The API-key and provider variables are stripped so the subscription login from `claude` is
used. The verdict is read from stdout, unwrapping the CLI JSON envelope when present; output
without a `verdict` object is a review failure.

## Failure And Recovery

`run_manifest.json` is the single durable record. It is rewritten atomically on every stage
transition and holds, per stage, the `status`, `current_attempt_id`, and an `attempts[]` list
whose entries carry each attempt's `local_state`, `response_id`, and the `pid` of the runner
process that opened it. A `.runner.lock` file in the run directory keeps two invocations from
touching the same run at once, including for the whole of a review.

Never duplicate-submit a live response. Once a stage holds a `response_id`, use `resume` or
`refresh` on it; `run` refuses it and prints the `resume` command. A `run` without `--stage`
never reruns a dead-end or abandoned stage implicitly either: it exits with the exact command
to use. The next command by stage status:

| stage status | next command |
|---|---|
| `submitted`, `in_progress`, `remote_terminal_pending_finalization`, `cancelling`, `finalized` | `resume --run-dir <run_dir> --stage <stage_id>`; a response is recorded and `resume` finishes it without a new request |
| `failed_no_artifact`, `blocked_preflight`, `failed_complete`, `cancelled`, `incomplete` | `run --run-dir <run_dir> --stage <stage_id>`; a dead end, rerun as a new attempt |
| `staging_inputs`, `uploading`, `preflight_passed` | `run --run-dir <run_dir> --stage <stage_id>`; abandoned before any request reached the API, refused while the attempt's recorded `pid` is still alive |
| `submitting`, `submission_outcome_unknown` | reconcile by hand (below); neither `run` nor `resume` touches it |
| `completed` with a `reviewed` gate and no `review_status` | `run --run-dir <run_dir>` (applies the pending review first), or add `--handoff-note <note.md>`, or `--reviewer none` |
| `waiting_for_review` | `run --run-dir <run_dir> --handoff-note <note.md>` |

A remote `failed`, `cancelled`, or `incomplete` response ends the stage as `failed_complete`,
`failed_no_artifact`, `cancelled`, or `incomplete`, the chain stops there, and the CLI exits
with code 2. Read the artifact (if any) and `response.final.json` before rerunning, and give
an output-limit `incomplete` a scope, model, or budget decision first. A rerun of `uploading`
or `preflight_passed` may leave files the abandoned attempt uploaded; `recover-uploads`
deletes them.

Reconciliation. `submitting` means the process died inside `POST /responses`;
`submission_outcome_unknown` means the POST failed in a way that does not prove whether the
request landed, and `submission.error.json` in the attempt directory has the error. In both,
a request may have reached the API without a recorded response id, so the runner never
resubmits: `run` prints the guidance below, `resume` and `refresh` refuse the stage (for
`submission_outcome_unknown`, "without operator reconciliation"), and `cancel` refuses it too.
Check the OpenAI dashboard for a response with metadata `stage_id=<stage_id>`. If one exists,
record its id as the stage's `response_id` with status `submitted` in `run_manifest.json` and
use `resume`; if none exists, set the stage status to `failed_no_artifact` there and rerun
with `--stage`. Keep the attempt directory as evidence. A definite POST failure becomes
`failed_no_artifact` on its own, with its uploads cleaned up.

Waiting failures. Polling a background response tolerates transient retrieve failures (HTTP
408, 409, 425, 429, 5xx, or network errors) for up to 30 consecutive polls; any other error,
or a longer outage, exits with the exact `resume` command while the response keeps running
remotely. Token preflight retries back off (2^attempt seconds, capped at 30 s), and the
reviewed gate keeps the stage's measured `token_preflight` in the manifest.

If a remote response is terminal but `artifact.md`, `response.final.json`, or
`output.structured.json` is missing, that is a local finalization gap: `resume` the stage to
write the final artifacts and apply the gate. `refresh` only records
`remote_terminal_pending_finalization`; it never backfills. A crash while finalizing leaves the
stage `remote_terminal_pending_finalization` (with `finalization.error.json` when finalization
itself raised) or `finalized`; `resume` completes either without issuing a new request.
`recover-uploads --run-dir <run_dir> --stage <stage_id> [--attempt <n>]` idempotently retries
deletion of one attempt's uploaded files.

## Validation

```bash
python -m unittest discover -s automation/tests -p 'test_*.py'
```

Focused checks after touching gates or the reviewer:

```bash
python -m unittest \
  automation.tests.test_responses_runner_v2_reviewed_gates \
  automation.tests.test_responses_runner_v2_gstack_pack \
  automation.tests.test_responses_runner_v2_model_migration
```

Then dry-run every stage of the pack you are about to launch (see above).
