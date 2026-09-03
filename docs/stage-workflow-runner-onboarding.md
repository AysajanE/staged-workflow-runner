# Stage Workflow Runner — Operator Onboarding Guide

> **Who this is for:** a new teammate who will *operate* the Stage Workflow Runner —
> dry-run a task pack, launch it, read what the reviewer said, unblock a stage with a
> handoff note, recover from a dead terminal, and hand over the final artifact. You do not
> need to modify the engine to operate it. You *do* need to know what every command does,
> what every file in a run directory means, and which guardrails stop you on purpose.
> Sections 1–4 are the mental model; 5–10 the reference; 11–15 are for the keyboard.

---

## Table of contents

1. [The one-sentence model](#1-the-one-sentence-model)
2. [First principles](#2-first-principles)
3. [One engine, gates between stages](#3-one-engine-gates-between-stages)
4. [Core vocabulary](#4-core-vocabulary)
5. [The engine in depth](#5-the-engine-in-depth)
6. [Anatomy of a single stage](#6-anatomy-of-a-single-stage)
7. [Before the first live stage: the all-stage dry run](#7-before-the-first-live-stage-the-all-stage-dry-run)
8. [The reviewed gate](#8-the-reviewed-gate)
9. [The workflows: modes and packs](#9-the-workflows-modes-and-packs)
10. [The run output layout](#10-the-run-output-layout)
11. [End-to-end walkthroughs](#11-end-to-end-walkthroughs)
12. [Command reference](#12-command-reference)
13. [Operator playbooks](#13-operator-playbooks)
14. [Guardrails you cannot bypass](#14-guardrails-you-cannot-bypass)
15. [Where to look when something is wrong](#15-where-to-look-when-something-is-wrong)
16. [Reading order and next steps](#16-reading-order-and-next-steps)

---

## 1. The one-sentence model

**The Stage Workflow Runner is a manifest-driven runner for high-stakes, staged OpenAI
Responses workflows: one engine runs the stages in order, a gate between each pair of
stages decides whether the run continues, and every artifact is written as hashed,
schema-versioned evidence under one workspace root.**

- **"high-stakes"** — an answer could be wrong in a way that costs money or trust, so
  every claim must trace back to an attached file with a known hash.
- **"staged"** — ordered **stages**, each with a **gate**: `auto`, `reviewed`, `human`,
  or `terminal`. A `reviewed` gate asks one reviewer CLI for a verdict; `human` waits for you.
- **"manifest-driven"** — you write JSON manifests and Markdown prompts in a **task
  pack** (`workflows/*.workflow.json`, `inputs/*.input_manifest.json`, `prompts/*.md`,
  `tools/*.profile.json`, `schemas/*.schema.json`, `shared_instructions.md`), not Python.
  The engine is generic; the task pack is the workflow.

---

## 2. First principles

### 2.1 One workspace root, mechanically enforced

Every invocation operates against **one exact workspace root**. Workflow files, task-pack
assets, uploaded attachments, run outputs, dry-run renders, review evidence, and handoff
notes must all live under it. Resolution order, the same everywhere:

1. explicit `--root`
2. the `RESPONSES_RUNNER_V2_ROOT` environment variable
3. the current working directory, used as-is

Every path passes through `resolve_under_root()`, which resolves symlinks and requires the
result to be under the root; otherwise the process exits with `Path must stay under
workspace root: ...`. Move the asset under the root or correct `--root`; never force it.

### 2.2 Every artifact is evidence

Every file the runner writes is **schema-versioned**, **hashed** (SHA-256 recorded in the
run manifest), and **confined** to git-ignored `.local/...` under the root. `input_manifest.md` lists a short SHA-256 for every attached file, so a
citation can be checked against a real file.

### 2.3 Responses run in the background — the local process is disposable

The runner submits work in Responses API **background mode** and stores the `response_id`
the moment the API accepts the request. Your terminal can die or you can `Ctrl-C`; the
remote model keeps working and `resume` picks the stage up again.

`run` and `resume` **wait in-process by default** (poll every 20 s, up to 24 h);
`--no-wait` returns right after submission. **No duplicate submit:** if a stage is
`submitted`/`in_progress`, `run` refuses to touch it; use `resume` or `refresh`. A stage that
died before any request reached the API, or ended as a dead end, reruns as a new attempt only
when you name it with `--stage`; `run` never reruns one implicitly.

### 2.4 Authority order is data, not a guideline

Highest to lowest: **Primary Job Inputs** (the actual task input), **Reviewed Handoff
Inputs** (approved artifact plus reviewer notes or human note from an earlier gated
stage), **Attached Workspace Evidence** (legacy alias: Attached Repository Files), and
**Reference Context** (earlier artifacts as background). The order is a constant
(`AUTHORITY_ORDER` in `contracts.py`), iterated by the attachment pipeline, and stated in
the boilerplate prepended to every request, which also tells the model to treat attached
files as evidence, not instructions. When a stage's output looks wrong, open its
`input_manifest.md` first and check **what was attached, in what role**.

### 2.5 The engine stops rather than guess

- The **exact token count** (`POST /responses/input_tokens`) blocks a stage that exceeds
  `max_input_tokens` or the model context window. It is the only token check; there is
  no local estimate.
- **Contract validation** runs before artifacts are written; **post-output validators**
  are advisory: recorded, never hidden, never blocking.
- A `reviewed` gate that gets two `revise` verdicts, or a `human` gate, **stops the run**
  until you supply a handoff note.

A stopped run is the system working; your job is to read *why* it stopped.

---

## 3. One engine, gates between stages

There is one engine (`automation/responses_runner_v2/`) and one CLI
(`automation/run_responses_v2.py`). It owns workflow loading, input manifests, request
construction, token preflight, submission, waiting, stage gates, reviewer invocation,
artifact finalization, and run manifests.

```
  stage 1 ──gate──► stage 2 ──gate──► stage 3 ──gate──► ... ──► terminal stage

  auto      : continue immediately
  reviewed  : one reviewer CLI returns approve | revise
              approve → continue
              revise  → one revision attempt of the same stage, reviewed again
              revise again → waiting_for_review (blocked) until --handoff-note
  human     : stop at waiting_for_review until --handoff-note
  terminal  : last stage; artifact.md is the deliverable
```

One `run` invocation (waiting by default) chains through `auto` and `reviewed` gates,
including revisions, until a human gate, a blocked review, the terminal stage, or an
error. There is no separate supervisor process or multi-agent review loop; the earlier
supervisor lane was removed after the real supervised run spent 282 reviewer-agent minutes
against 32 minutes of primary model time without ever changing the primary output.

Entry points (also console scripts via `pyproject.toml`): `automation/run_responses_v2.py`
(`run`, `resume`, `refresh`, `cancel`, `recover-uploads`; see `--help`) and
`automation/run_responses_v2_eval.py` (eval datasets and freeze gates).

---

## 4. Core vocabulary

| Term | What it is |
|---|---|
| **Task pack** | A directory of JSON + Markdown that defines a workflow. No Python. |
| **Workflow manifest** | `*.workflow.json`: workflow id, mode, model roles, request defaults, `defaults.review`, and the ordered stages (one Responses API call each) with gate, carry-forward, optional per-stage `review`, and validators. |
| **Gate** | `auto`, `reviewed`, `human`, or `terminal`. (`review_required` is a legacy spelling that the loader reads as `human`; do not use it in new packs.) |
| **Attempt** | One execution of a stage: `attempt_001` first; a reviewer-requested revision is `attempt_002` with `revision_of_attempt_id` set. |
| **Verdict** | The reviewer's JSON: `{verdict: approve\|revise, summary, blocking_findings[], notes[]}`. |
| **Handoff note** | Markdown passed with `--handoff-note` to approve a stage waiting at a human gate or a blocked reviewed gate, or a completed reviewed stage whose review is still pending after a reviewer failure; attached to the next stage as a Reviewed Handoff Input. |
| **Run manifest** | `run_manifest.json`, the single durable record: run status, `stage_order`, the operator inputs the run was started with, and a per-stage summary whose `attempts[]` carry each attempt's `local_state` and `response_id`. Rewritten atomically on every stage transition. |

---

## 5. The engine in depth

Modules under `automation/responses_runner_v2/` you will read when operating:

```
contracts.py ────► AUTHORITY_ORDER, MODEL_CAPS, GateType, StageStatus, ReviewConfig,
                   REVISION_INSTRUCTIONS, resolve_under_root().
pack_loader.py ──► JSON → validated dataclasses; rejects bad model posture, stage shape,
                   carry-forward, and review config at load time.
workflow.py ─────► the orchestrator: run_workflow(), resume_stage(), refresh_stage(),
                   cancel_stage(), the state machine, and the gate logic.
reviewer.py ─────► the reviewed gate: build the job, run the CLI once, normalize the verdict,
                   write review/ evidence.
attachments.py ──► resolve, hash, wrap, limit, render input_manifest.md, upload, build content.
artifacts.py ────► run and attempt directories, the run-manifest writer, artifact.md.
validators.py ───► advisory post-output validators; openai_client.py ► urllib client.
```

The durability slimming is done: `run_manifest.json` is the single durable record, guarded by
`locking.py` (`.runner.lock`). Everything else under an attempt directory is evidence, not state.

### The stage loop

```
load_workflow_definition             ← validates manifest and model caps
load or create run_manifest.json
apply --handoff-note (if given)      ← marks the waiting stage human_approved
apply any pending review             ← a reviewed stage that completed but was never reviewed
_determine_next_stage                ← first prepared/revision_requested stage whose predecessor
        │                               is completed or has an approved handoff
        ▼
┌───────────────── per-stage loop ──────────────────────────────────────┐
│ 1. resolve attachments (static + operator inputs + handoff + carry-   │
│    forward + revision inputs); render input_manifest.{json,md}        │
│ 2. stage upload copies under upload_inputs/; build the request payload│
│ 3. --dry-run: write request_payload.json, move to the next stage      │
│ 4. live: upload files (uploads.json); exact token preflight           │
│ 5. POST /responses once; persist the response_id immediately          │
│ 6. wait: poll, rewriting response.latest.json                         │
│ 7. finalize: artifact.md, response.final.json, validators             │
│ 8. gate: reviewed → run the reviewer; human → stop; auto → continue   │
│ 9. revision_requested → rerun the same stage as the next attempt      │
│10. completed + next stage + (auto|reviewed) + no --stage → next stage │
└────────────────────────────────────────────────────────────────────────┘
```

- **Chaining needs all of:** the stage completed (and, for `reviewed`, was approved), a
  next stage exists, no `--stage` pin, no `--no-wait`.
- A `human` gate with a next stage ends in `waiting_for_review`; a `reviewed` gate ends
  `completed` (`review_status: approved`) or `waiting_for_review` (`review_status: blocked`).
- The reviewer runs on the **finalization path** (`run` waiting, or `resume`), under the
  run lock. If a stage completes while you are not waiting, `resume` finalizes it and applies
  the gate; a stage left `completed` with its review pending is reviewed first by the next
  `run --run-dir <run_dir>`.

---

## 6. Anatomy of a single stage

### 6.1 Building the request

`instructions` = `COMMON_RUNNER_INSTRUCTIONS` + `shared_instructions.md` + optional
per-stage instructions. The input content is the stage task text (prefixed with
`REVISION_INSTRUCTIONS` on a revision attempt), then `input_manifest.md` as the model's
table of contents, then the attached files in authority order, each group preceded by a
text label naming its role.

### 6.2 What lands in Reviewed Handoff Inputs

| Situation | Attached |
|---|---|
| `handoff_from_stage_id` names a `reviewed` stage the reviewer approved | its `artifact.md` + `review/reviewer_notes.md` |
| ...names a `human` stage you approved | its `artifact.md` + your handoff note |
| ...names a blocked `reviewed` stage you unblocked | `artifact.md` + your note + the reviewer notes |
| this attempt is a revision | the previous attempt's `artifact.md` + its `reviewer_notes.md` |

`handoff_from_stage_id` must point backward at a `reviewed` or `human` stage.

### 6.3 The attachment pipeline

Each entry is resolved (directories walked, skipping `.git`, `.local`, `node_modules`,
`__pycache__`; `exclude_globs` applied; every file hashed), wrapped if needed (UTF-8 text
that is not a Responses context type, such as `.go` or `.sql`, gets a Markdown wrapper
with the source path in front matter), rendered, uploaded, and turned into content blocks.
Limits, enforced before any HTTP traffic: 50 MB per file, 50 MB combined, 100 files per
request (a whole role is bundled into one Markdown file before giving up).

### 6.4 Stage status vs. response status

| Response status | Stage status |
|---|---|
| `completed`, gate `human`, has next | `waiting_for_review` |
| `completed`, gate `reviewed` | `completed`, then the gate decides: `approved`, `revision_requested`, `blocked` |
| `completed`, otherwise | `completed` |
| `failed` with / without output text | `failed_complete` / `failed_no_artifact` — a dead end; rerun with `--stage` |
| `cancelled` / `incomplete` | `cancelled` / `incomplete` — never auto-progresses; rerun with `--stage` |
| `queued` / `in_progress` | `in_progress` — use `resume`/`refresh`, never re-`run` |

### 6.5 Validators and structured output

`post_output_validators` run against `artifact.md` after finalization and write
`validator_report.json`; the run manifest records `validators_passed` and
`validator_report_path`. A failure prints `WARNING [validator_failed] ...` and nothing
else. A stage whose `output.primary_format` is `json_schema` (structural model role) also
writes `output.structured.json`.

---

## 7. Before the first live stage: the all-stage dry run

The whole pre-launch check is one command. `run --dry-run` without `--stage` renders
**every** stage's request without uploading anything or calling the API:

```bash
python automation/run_responses_v2.py run --root . \
  --workflow-file automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json \
  --primary-job-input docs/runbooks/first-use-adaptation-example.md \
  --dry-run
```

It proves the workflow loads, every input manifest resolves under the root, every
carry-forward points at a real earlier stage, and every request payload is well-formed:

```
<run_dir>/
├── run_manifest.json                     status "created"
└── dry_runs/
    ├── stages/NN_<stage_id>/             one per stage
    │   ├── input_manifest.json / .md
    │   ├── request_payload.json          the exact body that would be sent
    │   └── upload_inputs/                staged copies of the files prepared for upload
    └── stubs/<stage_id>/                 placeholders for handoffs from stages not yet run
        ├── artifact.md
        └── handoff_notes.md
```

Later stages reference the stubs so their requests render before earlier stages exist.
Read each `dry_runs/stages/*/input_manifest.md` and confirm the attachments and roles.
The exact token count runs live only; a dry run never calls the API. Pin `--stage <id>`
to dry-run one stage. CI runs this on every push.

---

## 8. The reviewed gate

### 8.1 One reviewer, one verdict

When a `reviewed` stage completes, the engine invokes **one** reviewer CLI, once, with a
bounded job: the stage task text, `artifact.md`, `input_manifest.md`, the handoff inputs,
`validator_report_path` when a validator report exists, and (on a revision) the id of the
attempt being revised. The prompt, `automation/responses_runner_v2/prompts/stage_review.md`,
tells the reviewer to read those files, to open any file listed in `input_manifest.md` only
to spot-check a material claim, to treat the validator report's violations as evidence, to
edit nothing, and to judge objective, grounding, consistency, and fitness for the next
stage. The verdict is validated against
`automation/responses_runner_v2/schemas/stage_review_verdict.schema.json`:

```json
{
  "verdict": "approve | revise",
  "summary": "two to five sentences",
  "blocking_findings": [{"id": "...", "description": "...", "evidence": "...", "required_change": "..."}],
  "notes": ["non-blocking observations for the next stage"]
}
```

Configuration: workflow `defaults.review`, overridden per stage by `review`.

| Field | Values | Default |
|---|---|---|
| `reviewer` | `codex`, `claude`, `none` | `codex` |
| `model` | any | `gpt-5.6-sol` for codex, `opus` for claude |
| `effort` | `low`, `medium`, `high`, `xhigh`, `max` | `high` for codex, `xhigh` for claude |
| `timeout_seconds` | positive int | `1800` |
| `max_revisions` | 0 or more | `1` |

`run --reviewer {codex,claude,none}` overrides the reviewer for that invocation. `none`
writes an approve verdict with `disposition: not_required` and continues.

Both CLIs run read-only from the workspace root: `codex exec --sandbox read-only
--ephemeral --ignore-user-config -c model_reasoning_effort=... --output-schema <verdict
schema> [--model ...] -` with the prompt on stdin; `claude -p --model opus --effort xhigh
--output-format json --tools Read,Grep,Glob --permission-mode dontAsk
--no-session-persistence --setting-sources user --append-system-prompt-file <prompt>`
with the job JSON on stdin and `ANTHROPIC_API_KEY` and related variables stripped so the
subscription login is used.

### 8.2 The three outcomes

```
approve ──► review_status "approved"; the run continues.
revise  ──► first time (revisions < max_revisions): stage becomes revision_requested and
            reruns as the next attempt with REVISION_INSTRUCTIONS ahead of the task and the
            previous artifact.md + reviewer_notes.md under Reviewed Handoff Inputs; the
            reviewer runs again on the revision.
revise  ──► again (revisions == max_revisions): stage becomes waiting_for_review with
            review_status "blocked". The run stops for a human.
```

### 8.3 Unblocking with a handoff note

Write a Markdown note under the root stating your decision and anything the next stage
must know, then continue the same run:

```bash
python automation/run_responses_v2.py run --root . \
  --workflow-file <wf> --run-dir <run_dir> --handoff-note notes/<stage>_handoff.md
```

The engine marks the latest waiting `human` or `reviewed` stage `human_approved`, records
`handoff_note_path`, and attaches the note (plus the reviewer notes, for a blocked reviewed
stage) with the artifact to the next stage. The same command continues a `human` gate, and
when no stage is waiting it approves a `completed` reviewed stage whose review is pending
(section 8.4).

### 8.4 When the reviewer CLI fails

If the reviewer exits non-zero, times out, cannot be spawned, or returns output without a
valid verdict (or, for codex, returns a verdict although its transcript never opened
`artifact.md`), the engine exits with the stderr path and the stage stays `completed` with no
`review_status`. The error names the continuation: rerun the same command with
`--run-dir <run_dir>` to retry the review first; add `--handoff-note <note.md>` to approve the
artifact yourself (`review_status: human_approved`); or add `--reviewer none`. A `run`
without `--run-dir` starts a new run and resubmits the stage at full cost. The review holds
the run lock, so a second `run` on the same run directory during a review is refused rather
than starting a duplicate reviewer.

### 8.5 The review evidence directory

```
attempt_NNN/review/
├── verdict.json               normalized verdict + disposition, reviewer, artifact_sha256
├── reviewer_notes.md          human-readable rendering; attached to the next stage / revision
├── prompt_<stamp>.md          the exact prompt + job JSON the reviewer saw
├── stdout_<stamp>.txt
├── stderr_<stamp>.txt
└── invocation_<stamp>.json    argv, cwd, duration_ms, exit_code, cost/token fields when reported
```                            (a retried review adds another stamped set)

---

## 9. The workflows: modes and packs

### 9.1 Workflow modes

| Mode | Stages | Typical gate pattern |
|---|---|---|
| `one_pass` | exactly 1 | `terminal` |
| `two_pass` | exactly 2 | `auto` → `terminal` |
| `reviewed_three_stage` | exactly 3 | gated → gated → `terminal` |
| `custom_ordered` | any number | any combination |

### 9.2 The bundled packs

- `automation/examples/responses_runner_v2_synthetic/` — the synthetic proof pack: a
  one-pass, a two-pass, and a three-stage gated workflow for verifying the engine.
- `automation/examples/responses_runner_v2_evidence_synthesis/` — an offline
  document-evidence example with citation validators.
- `automation/task_packs/gstack_design_to_po_playbook/` — the real high-stakes pack: five
  `custom_ordered` stages, `defaults.review = {reviewer: codex, max_revisions: 1}`:

```
1 source_authority_map      reviewed
2 repo_grounding            reviewed   handoff_from_stage_id: source_authority_map
3 execution_row_draft       reviewed   handoff_from_stage_id: repo_grounding
4 gate_and_contract_review  reviewed   handoff_from_stage_id: execution_row_draft
5 final_markdown_playbook   terminal   handoff_from_stage_id: gate_and_contract_review
```

Each stage also carries earlier artifacts as `reference_context_from_stage_ids`.

### 9.3 Carry-forward

- **`handoff_from_stage_id`** — the approved artifact plus reviewer notes or human note
  of an earlier `reviewed`/`human` stage, under Reviewed Handoff Inputs. The gated path.
- **`reference_context_from_stage_ids`** — earlier `artifact.md` files under Reference
  Context. Cheap, no gate involved.
- `review_bundle_from_stage_id` is a legacy alias; write `handoff_from_stage_id` instead.

Carry-forward attaches the actual approved text, never a summary.

### 9.4 The locked model posture

The loader rejects anything else: primary generation `gpt-5.6` with `reasoning_mode=pro`,
effort `xhigh`, verbosity `high`; structural processing `gpt-5.6`, standard mode, effort
`high` or `medium`; prompt cache implicit with `ttl=30m`; max output tokens `128000`. Keep
existing verbosity and terminal-stage effort settings until an A/B measurement supports
changing them. Do not introduce legacy 5.4-family identifiers.

---

## 10. The run output layout

Everything lands under `.local/` — git-ignored, never committed, never re-attached.

```
.local/automation/responses_runner_v2/runs/{timestamp}_{run_name}_{workflow_id}/
├── run_manifest.json                    run state + per-stage summary
├── dry_runs/                            see section 7
│   ├── stages/NN_<stage_id>/...
│   └── stubs/<stage_id>/...
└── stages/NN_<stage_id>/
    ├── attempt_001/
    │   ├── input_manifest.json / .md    resolved attachments (machine / human + model TOC)
    │   ├── upload_inputs/               staged copies of the files prepared for upload
    │   ├── request_payload.json         the EXACT body sent to /responses
    │   ├── token_preflight.json         or token_preflight.error.json
    │   ├── uploads.json                 file_id ↔ source path + lifecycle
    │   ├── response.latest.json         rewritten on every poll
    │   ├── response.final.json          raw retained terminal response
    │   ├── artifact.md                  the clean deliverable used downstream
    │   ├── validator_report.json        if validators configured
    │   ├── output.structured.json       if primary_format is json_schema
    │   ├── submission.error.json        only if POST /responses failed
    │   ├── finalization.error.json      only if finalization raised
    │   └── review/                      reviewed-gate evidence (section 8.5)
    └── attempt_002/                     a revision; same layout, revision_of_attempt_id set
```

Run-manifest stage summary fields you will read: `status`, `review_status`
(`approved` | `revision_requested` | `blocked` | `human_approved` | `not_required`),
`reviewer_notes_path`, `review_verdict_path`, `handoff_note_path`, `validators_passed`,
`validator_report_path`, `artifact_markdown_path` (+ sha256), `response_id`,
`current_attempt_id`, and `attempts[]` with `attempt_dir`, `local_state`, `response_id`, and
`revision_of_attempt_id`. There is no per-attempt state file: the run manifest is the only
durable record, rewritten atomically on every stage transition.

---

## 11. End-to-end walkthroughs

### Path A — one-pass synthetic workflow (fastest way to see it work)

```bash
# 1. Dry-run: build the request, touch no API.
python automation/run_responses_v2.py run --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json --dry-run

# 2. Live run (waits by default): uploads, submits, polls, writes artifact.md, prints the manifest path.
python automation/run_responses_v2.py run --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json
```

### Path B — the gstack pack with reviewed gates

```bash
WF=automation/task_packs/gstack_design_to_po_playbook/workflows/gstack_design_to_po_playbook.workflow.json

# 1. All-stage dry run. Read every dry_runs/stages/*/input_manifest.md.
python automation/run_responses_v2.py run --root . --workflow-file $WF \
  --primary-job-input docs/gstack/<approved-design>.md --dry-run

# 2. Launch. One invocation runs stage 1, reviews it, revises once if asked, then stages
#    2-4 the same way, then terminal stage 5. stderr shows lines such as
#      REVIEW [repo_grounding/attempt_001] revise -> revision_requested (...)
python automation/run_responses_v2.py run --root . --workflow-file $WF \
  --primary-job-input docs/gstack/<approved-design>.md

# 3a. Finished: the deliverable is stages/05_final_markdown_playbook/attempt_001/artifact.md.
# 3b. Stopped "waiting for a handoff note after stage <id>" (two revise verdicts, or a
#     human gate): read that stage's artifact.md and review/reviewer_notes.md, write your
#     decision, and continue the same run.
python automation/run_responses_v2.py run --root . --workflow-file $WF \
  --run-dir <run_dir> --handoff-note notes/<stage>_handoff.md

# 3c. Exit code 2 with "WARNING run did not succeed": a stage ended as a dead end. Read its
#     attempt directory, then rerun that stage as a new attempt in the same run.
python automation/run_responses_v2.py run --root . --workflow-file $WF \
  --run-dir <run_dir> --stage <stage_id>
```

---

## 12. Command reference

All subcommands accept `--root`. `run`/`resume` print the `run_manifest.json` path on
success; warnings go to stderr as `WARNING [<code>] ...`. `run` also prints `RUN_DIR <path>`
to stderr as soon as the run directory is known, and both print
`RUN <run_status> stage <stage_id> <stage_status>` when they finish (never on a dry run): a
failed, blocked, cancelled, incomplete, or unknown-submission outcome adds a `WARNING` with the
rerun command and exits with code 2, and `waiting_for_review` adds a `WAITING` hint. Run
`python automation/run_responses_v2.py --help` for the full current subcommand list.

### `run` — launch the next eligible stage or continue a run

| Flag | Meaning |
|---|---|
| `--workflow-file <path>` | required; interpreted under the root |
| `--run-name <slug>` / `--run-dir <path>` | name a new run (defaults to the workflow id) / continue an existing run |
| `--stage <id>` | pin one stage; disables chaining; required to rerun a dead-end or abandoned stage as a new attempt |
| `--primary-job-input`, `--reference-context` (repeatable), `--input-binding-file` | operator inputs; bindings are stage-scoped |
| `--handoff-note <path>` | approve the stage waiting at a human gate or blocked reviewed gate, or a completed reviewed stage whose review is pending |
| `--reviewer {codex,claude,none}` | override the reviewer for reviewed gates in this invocation |
| `--dry-run` | render every stage (or the pinned stage) under `dry_runs/`; no uploads, no API |
| `--wait` / `--no-wait`, `--poll-interval <s>`, `--max-wait-seconds <s>` | wait in-process (default; poll 20 s, cap 86400 s) or return after submission |
| `--skip-token-count`, `--max-input-tokens <n>`, `--max-output-tokens <n>` | disable the exact count / override stage limits |
| `--primary-model` / `--structural-model` | model overrides (must satisfy model caps) |
| `--output-root <path>` | default `.local/automation/responses_runner_v2/runs` |
| `--file-expires-after`, `--delete-uploaded-files-on-complete`, `--service-tier`, `--safety-identifier`, `--prompt-cache-key-strategy` | upload lifecycle and request options |

### The others

| Subcommand | Flags | What it does |
|---|---|---|
| `resume` | `--run-dir`, `--stage`, `--wait`/`--no-wait`, `--poll-interval`, `--max-wait-seconds` | Finish a submitted stage from its stored `response_id`: finalize artifacts, validators, and the reviewed gate. Refuses `submission_outcome_unknown` and any stage without a recorded response. |
| `refresh` | `--run-dir`, `--stage` | Record the latest remote status only. No finalization, no review. |
| `cancel` | `--run-dir`, `--stage` | Idempotently cancel a live response and finalize local evidence. |
| `recover-uploads` | `--run-dir`, `--stage`, `--attempt <n>` | Resume cleanup of one attempt's uploads. |

`run_responses_v2_eval.py`: `--dataset-file --list-cases`; or `--dataset-file --case-id
--artifact [--structured-artifact]` to grade one case; or `--freeze-gate-file`.

---

## 13. Operator playbooks

**"I want to prove the runner works at all."** The tests plus the dry runs are the gate CI
runs on Python 3.10/3.11/3.12.

```bash
python -m unittest discover -s automation/tests -p 'test_*.py'
python automation/run_responses_v2.py run --root . \
  --workflow-file automation/examples/responses_runner_v2_synthetic/workflows/one_pass.workflow.json --dry-run
```

**"My terminal died / I `Ctrl-C`'d during a run."** The stderr `RUN_DIR` line names the run
directory; read the stage `status` in its `run_manifest.json`, or just rerun
`run --run-dir <run_dir>`: it refuses and prints the exact next command. With a recorded
response (`submitted`, `in_progress`, `remote_terminal_pending_finalization`, `cancelling`,
`finalized`), `resume` polls to terminal, finalizes, and runs the reviewed gate if there is
one; then `run --run-dir <run_dir>` continues the chain.

```bash
python automation/run_responses_v2.py resume --root . --run-dir <run_dir> --stage <stage_id>
```

Died before any request reached the API (`staging_inputs`, `uploading`, `preflight_passed`)?
Nothing is running remotely: rerun the stage as a new attempt with
`run --run-dir <run_dir> --stage <stage_id>`. The rerun is refused while the attempt's
recorded `pid` is still alive, and `recover-uploads` deletes files an abandoned upload left
behind. Died inside the POST (`submitting`)? Reconcile as for `submission_outcome_unknown`
below.

**"The reviewer CLI failed."** The stage is `completed` with no `review_status`, and the
error names the continuation. Read `attempt_NNN/review/stderr_*.txt`, fix the CLI (login,
PATH, timeout), and rerun the same command with `--run-dir <run_dir>`; the review retries
first. Alternatives on that same command: `--reviewer claude`, `--reviewer none`, or
`--handoff-note <note.md>` to approve the artifact yourself (`review_status: human_approved`).
Do not drop `--run-dir`: a bare `run` starts a new run and resubmits the stage at full cost.
A codex verdict whose transcript never opened `artifact.md` is rejected into the same state.

**"The run stopped: waiting for a handoff note."** A `human` gate, or a `reviewed` gate
with `review_status: blocked` after two `revise` verdicts. Read the latest attempt's
`artifact.md` and `review/reviewer_notes.md`, write a note with your decision and any
corrections, and continue with `run --run-dir <run_dir> --handoff-note <note.md>`.

**"Token preflight blocked the stage."** The exact count exceeded `max_input_tokens` or
the context window (minus output budget and margin), or the count service failed closed.
Read `token_preflight.error.json`; reduce the input manifest scope or raise the stage
limit deliberately. The stage is left `blocked_preflight`; rerun it in place as a new
attempt with `run --run-dir <run_dir> --stage <stage_id>`.

**"A stage failed, was cancelled, or came back `incomplete`."** `failed_complete`: the
model reported failure but wrote an artifact; read it first. `failed_no_artifact`: nothing
usable came back. `incomplete`: the output limit was hit; decide on scope, model, or budget
before spending again. All of these, plus `cancelled` and `blocked_preflight`, are dead
ends: the CLI prints a `WARNING` and exits 2, and `run --run-dir <run_dir>` without `--stage`
refuses and prints the command. Rerun the stage in place as a new attempt with
`run --run-dir <run_dir> --stage <stage_id>`, or start a new run (omit `--run-dir`). Keep the
failed directory as evidence. A revision attempt that died this way keeps its
`revision_of_attempt_id` on rerun and does not spend the revision budget.

**"A stage is `submission_outcome_unknown` (or `submitting`)."** The `POST /responses`
failed in a way that does not prove whether the request landed
(`attempt_NNN/submission.error.json` has the error), or the process died inside the POST. A
request may have reached the API without a recorded response id, so nothing resubmits it:
`run` prints the reconciliation guidance, `resume` and `refresh` refuse it without operator
reconciliation, and `cancel` refuses it too. Check the OpenAI dashboard for a response with
metadata `stage_id=<stage_id>`. If one exists, record its id as the stage's `response_id`
with status `submitted` in `run_manifest.json` and `resume`; if none exists, set the stage
status to `failed_no_artifact` there and rerun with `--stage`. Keep the directory as evidence.

**"A stage has been `in_progress` for hours."** Do not re-`run` it. `refresh` records the
status; `resume` keeps polling; `cancel` cancels the remote response and finalizes what exists.
A waiting `run` rides out transient retrieve errors (HTTP 408, 409, 425, 429, 5xx, network)
for up to 30 consecutive polls, then exits with the exact `resume` command; the response
keeps running remotely.

**"I'm using this checkout against a different project."** Put the task pack and every
referenced asset under the *target* workspace root and invoke with `--root /path/to/target`;
`--workflow-file` and `.env` are resolved under that root. There is no dual-root mode.

---

## 14. Guardrails you cannot bypass

| Guardrail | Trips when | Where |
|---|---|---|
| **One-root** | any path resolves outside the workspace root | `resolve_under_root()` |
| **Model caps** | `max_output_tokens` over the cap, wrong cache TTL, structured output on an unsupported model | `validate_model_options()` |
| **GPT-5.6 cache posture** | a role omits implicit cache mode with `ttl=30m` | `pack_loader` |
| **background + store** | `background=true` with `store=false` | `pack_loader` |
| **Stage shape / review config** | duplicate ids, mis-ordered numbers, mode/count mismatch, dangling carry-forward, handoff source not `reviewed`/`human`, unknown `reviewer`/`effort`, negative `max_revisions` | `pack_loader` |
| **No-duplicate-submit** | `run` on a stage that is submitted, in progress, `submitting`, or `submission_outcome_unknown`; an implicit rerun of a dead-end or abandoned stage without `--stage`; a pre-submission rerun while the attempt's recorded `pid` is alive | `_determine_next_stage`, `_refuse_if_attempt_is_live` |
| **Gate order** | running past a waiting `human` or blocked `reviewed` stage without `--handoff-note` | `_determine_next_stage` |
| **Exact token preflight** | input tokens exceed the stage or context limit | `_token_preflight_state` |
| **Verdict contract** | reviewer output has no `approve`/`revise` verdict or fails the schema; a codex verdict whose transcript never opened `artifact.md` | `reviewer.normalize_verdict`, `reviewer.reviewer_read_artifact` |
| **Run lock / state transitions** | two processes touch one run directory (the lock is held for the whole review); an illegal stage transition | `.runner.lock`, `assert_stage_transition` |

---

## 15. Where to look when something is wrong

```
Question                                First file to open
────────────────────────────────────────────────────────────────────────────
"Why did the model produce that?"    →  attempt_NNN/input_manifest.md (what was attached, in which role)
"What exactly did we send?"          →  attempt_NNN/request_payload.json
"What did the reviewer object to?"   →  attempt_NNN/review/reviewer_notes.md
"Why did the review fail to run?"    →  attempt_NNN/review/stderr_*.txt and invocation_*.json
"What is the current stage state?"   →  run_manifest.json stages[] (status, current_attempt_id, attempts[].response_id)
"What is the overall run state?"     →  run_manifest.json (status, current_stage_id, review_status)
"Which command continues this run?"  →  rerun `run --run-dir <run_dir>`; its refusal or WARNING names the command
"Did token preflight pass?"          →  attempt_NNN/token_preflight.json or token_preflight.error.json
"Did a validator complain?"          →  attempt_NNN/validator_report.json
"What is the clean deliverable?"     →  attempt_NNN/artifact.md
"What raw response was retained?"    →  attempt_NNN/response.final.json
"Would this pack even build?"        →  dry_runs/stages/*/request_payload.json
```

A `SystemExit` message from the CLI names the guardrail that tripped; read it literally.

---

## 16. Reading order and next steps

1. `AGENTS.md` and `DEVELOPING.md` — the repo-level rules and the developer mental model.
2. `docs/runbooks/responses-runner-v2.md` — the day-to-day operator runbook.
3. `automation/responses_runner_v2/contracts.py` (`GateType`, `StageStatus`,
   `ReviewConfig`, `REVISION_INSTRUCTIONS`) and `workflow.py` (`_apply_stage_gate`,
   `_gate_handoff_entries`, `_apply_handoff_note` are the gate logic).
4. `automation/responses_runner_v2/reviewer.py` and `prompts/stage_review.md` — what the
   reviewer sees and how its answer is normalized.
5. `automation/tests/test_responses_runner_v2_reviewed_gates.py` — the executable spec —
   and `automation/task_packs/gstack_design_to_po_playbook/README.md` — the real pack.

**The single fastest way to build intuition:** dry-run the gstack pack and open every file
under `<run_dir>/dry_runs/`; then run the synthetic one-pass workflow live and open every
file under its `attempt_001/`. The engine's whole contract is visible there.
